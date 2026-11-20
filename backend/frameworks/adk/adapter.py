"""Google ADK framework (Mode A: stage executor), on the shared Gemini model.

ADK-native pieces:
- intake: ``LlmAgent`` with a ``search_partners`` tool that goes through the platform ToolGateway
  (allowlist + audit log), so the agent decides what to look up.
- planning: ``SequentialAgent`` [planner -> critic]; the critic reads the draft from session
  state (``output_key``) and returns a corrected plan (a self-refinement step inside ADK).
- reporting: ``LlmAgent``.
Research / policy / data retrieval stay deterministic (shared with ``rules``), as in the Gemini
baseline, so the comparison isolates the reasoning stages. ADK objects never leave this module.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from google.adk.agents import LlmAgent, SequentialAgent
from google.adk.agents.base_agent import BaseAgent
from google.adk.models import BaseLlm
from google.adk.runners import InMemoryRunner
from google.genai import types

from backend.domain.schemas import ActionPlan, Classification, Evidence, Report
from backend.frameworks.base import AgentTaskRequest, TaskType, TokenUsage
from backend.frameworks.gemini.adapter import catalog_text, load_prompt, untrusted
from backend.frameworks.rules.adapter import RulesAdapter
from backend.llm.client import COST_PER_1M, LLMError, _extract_json
from backend.tools.gateway import ToolGateway

APP = "workflowops"
CRITIC_INSTRUCTION = """You are the Plan Critic. Review the DRAFT PLAN below against the policies
and the action catalog given earlier in the conversation. Remove actions that violate policy,
fix wrong params or missing evidence_refs, keep valid actions unchanged. Return ONLY the final
plan JSON in exactly the same format as the draft.

DRAFT PLAN:
{draft_plan}"""


class ADKAdapter(RulesAdapter):
    name = "adk"
    version = "2.15.0"

    def __init__(self, gateway: ToolGateway, model: str | BaseLlm) -> None:
        super().__init__(gateway)
        self.model = model
        self.model_name = model if isinstance(model, str) else getattr(model, "model", "adk")
        self._usage: dict[uuid.UUID, TokenUsage] = {}

    def _pop_usage(self, request: AgentTaskRequest) -> tuple[TokenUsage, str | None]:
        u = self._usage.pop(request.execution_id, None)
        return (u, self.model_name) if u else (TokenUsage(), None)

    def _cfg(self) -> types.GenerateContentConfig:
        # Bounded retries with backoff for overload / rate limits (429/5xx).
        return types.GenerateContentConfig(
            temperature=0.0,
            http_options=types.HttpOptions(retry_options=types.HttpRetryOptions(attempts=4)),
        )

    async def _run(
        self, req: AgentTaskRequest, agent: BaseAgent, prompt: str
    ) -> tuple[str, dict[str, Any]]:
        """Run an ADK agent tree once; return the final text and the session state."""
        runner = InMemoryRunner(agent=agent, app_name=APP)
        session = await runner.session_service.create_session(app_name=APP, user_id="platform")
        final, tin, tout = "", 0, 0
        async for event in runner.run_async(
            user_id="platform",
            session_id=session.id,
            new_message=types.Content(role="user", parts=[types.Part(text=prompt)]),
        ):
            meta = getattr(event, "usage_metadata", None)
            if meta:
                tin += meta.prompt_token_count or 0
                tout += (meta.candidates_token_count or 0) + (meta.thoughts_token_count or 0)
            if event.is_final_response() and event.content and event.content.parts:
                final = "".join(p.text or "" for p in event.content.parts)
        done = await runner.session_service.get_session(
            app_name=APP, user_id="platform", session_id=session.id
        )
        state = done.state if done else {}
        cin, cout = COST_PER_1M.get(self.model_name, (0.0, 0.0))
        u = self._usage.setdefault(req.execution_id, TokenUsage())
        u.input_tokens += tin
        u.output_tokens += tout
        u.cost_usd_estimate += (tin * cin + tout * cout) / 1_000_000
        return final, dict(state)

    @staticmethod
    def _parse[T: (Classification, ActionPlan, Report)](text: str, schema: type[T]) -> T:
        try:
            return schema.model_validate(_extract_json(text))
        except Exception as e:
            raise LLMError(f"ADK agent output failed {schema.__name__}: {str(e)[:300]}") from e

    async def _dispatch(
        self, req: AgentTaskRequest, tool: Any
    ) -> tuple[dict[str, Any], list[Evidence]]:
        match req.task_type:
            case TaskType.CLASSIFY:
                return await self._adk_classify(req, tool)
            case TaskType.PLAN:
                return await self._adk_plan(req)
            case TaskType.REPORT:
                return await self._adk_report(req)
            case _:
                return await super()._dispatch(req, tool)

    async def _adk_classify(
        self, req: AgentTaskRequest, tool: Any
    ) -> tuple[dict[str, Any], list[Evidence]]:
        found: dict[str, dict[str, Any]] = {}

        async def search_partners(query: str) -> list[dict[str, str]]:
            """Search the CRM for partners whose name contains `query` (case-insensitive).

            Returns a list of {"id", "name", "account_status"}. Use short queries such as a
            single distinctive word from the partner's name.
            """
            hits = await tool("crm.search_partners", q=query)
            for h in hits:
                found[h["name"]] = h
            return [
                {"id": h["id"], "name": h["name"], "account_status": h["account_status"]}
                for h in hits
            ]

        agent = LlmAgent(
            name="intake",
            model=self.model,
            generate_content_config=self._cfg(),
            instruction=load_prompt("intake").replace(
                "copied exactly from the CANDIDATE PARTNERS list",
                "copied exactly from a search_partners tool result (search before answering)",
            ),
            tools=[search_partners],
        )
        prompt = (
            f"REQUEST (priority {req.input.get('priority', 'normal')}):\n"
            f"{untrusted(req.input['description'])}"
        )
        text, _ = await self._run(req, agent, prompt)
        cls = self._parse(text, Classification)
        partner = found.get(cls.partner_name or "")  # platform guard: id comes from the CRM
        if partner is None and cls.partner_name:
            cls.missing_info = sorted({*cls.missing_info, "partner_name"})
            cls.partner_name = None
        cls.partner_id = partner["id"] if partner else None
        ev = (
            [
                Evidence(
                    ref=f"crm:{partner['id']}",
                    source="crm",
                    summary=f"CRM record for {partner['name']}",
                )
            ]
            if partner
            else []
        )
        return cls.model_dump(), ev

    async def _adk_plan(self, req: AgentTaskRequest) -> tuple[dict[str, Any], list[Evidence]]:
        inp = req.input
        reports = inp["reports"]
        evidence_refs = sorted(
            {e["ref"] for r in reports.values() for e in r.get("evidence", [])}
            | {f"crm:{inp['classification'].get('partner_id')}"}
        )
        findings = [
            f
            for r in reports.values()
            for f in r.get("findings", [])
            if f["code"] != "applicable_policy"
        ]
        policies = [
            f["detail"] + " " + ",".join(f["evidence_refs"])
            for f in reports.get("policy", {}).get("findings", [])
            if f["code"] == "applicable_policy"
        ]
        prompt = "\n\n".join(
            [
                f"CLASSIFICATION: {json.dumps(inp['classification'])}",
                f"REQUESTER ID: {inp['requester_id']}",
                f"PARTNER FACTS: {json.dumps(reports.get('data', {}).get('facts', {}))}",
                f"FINDINGS: {json.dumps(findings)}",
                f"POLICIES: {json.dumps(policies)}",
                f"AVAILABLE EVIDENCE: {json.dumps(evidence_refs)}",
                f"ACTION CATALOG:\n{catalog_text()}",
                f"VALIDATION FEEDBACK: {json.dumps(inp.get('feedback', []))}",
            ]
        )
        planner = LlmAgent(
            name="planner",
            model=self.model,
            generate_content_config=self._cfg(),
            instruction=load_prompt("planning"),
            output_key="draft_plan",
        )
        critic = LlmAgent(
            name="critic",
            model=self.model,
            generate_content_config=self._cfg(),
            instruction=CRITIC_INSTRUCTION,
            output_key="final_plan",
        )
        text, state = await self._run(
            req, SequentialAgent(name="planning", sub_agents=[planner, critic]), prompt
        )
        plan = self._parse(state.get("final_plan") or text, ActionPlan)
        return plan.model_dump(), []

    async def _adk_report(self, req: AgentTaskRequest) -> tuple[dict[str, Any], list[Evidence]]:
        inp = req.input
        agent = LlmAgent(
            name="reporting",
            model=self.model,
            generate_content_config=self._cfg(),
            instruction=load_prompt("reporting"),
        )
        prompt = (
            f"PARTNER: {inp.get('partner_name')}\nOUTCOME: {inp['outcome']}\n"
            f"REASON: {inp.get('reason')}\nEXECUTION RECORDS: {json.dumps(inp['actions'])}"
        )
        text, _ = await self._run(req, agent, prompt)
        data = _extract_json(text) if text else {}
        if isinstance(data, dict):
            data["outcome"] = inp["outcome"]  # the platform decides the outcome
        return self._parse(json.dumps(data), Report).model_dump(), []
