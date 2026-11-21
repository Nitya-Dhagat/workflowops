"""LLM baseline framework on Google Gemini (single model, plain prompt per agent).

LLM-driven agents: intake (classification), planning, reporting.
Deterministic, tool-based agents (shared with ``rules``): research, policy, data retrieval, and
the advisory validation step. The deterministic policy engine stays authoritative either way.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from backend.domain.enums import RequestStatus
from backend.domain.schemas import ActionPlan, Classification, Evidence, Report
from backend.frameworks.base import AgentTaskRequest, TaskType, TokenUsage
from backend.frameworks.rules.adapter import RulesAdapter
from backend.llm.client import LLMClient, LLMUsage, structured
from backend.policy.catalog import CATALOG
from backend.tools.gateway import ToolGateway

AGENTS_DIR = Path(__file__).resolve().parents[2] / "agents"
PROMPT_VERSION = "v1"


def load_prompt(agent: str, version: str = PROMPT_VERSION) -> str:
    return (AGENTS_DIR / agent / "prompts" / f"{version}.md").read_text(encoding="utf-8")


def untrusted(text: str) -> str:
    """Delimit and label data so the model treats it as content, never as instructions."""
    safe = text.replace("</untrusted_data>", "")
    return f"<untrusted_data>\n{safe}\n</untrusted_data>"


def catalog_text() -> str:
    lines = []
    for spec in CATALOG.values():
        fields = {
            name: str(f.annotation).replace("typing.", "")
            for name, f in spec.params_model.model_fields.items()
        }
        lines.append(f"- {spec.name} (risk={spec.risk.value}): params {json.dumps(fields)}")
    return "\n".join(lines)


class GeminiAdapter(RulesAdapter):
    name = "gemini"
    version = "1.0.0"

    def __init__(self, gateway: ToolGateway, llm: LLMClient) -> None:
        super().__init__(gateway)
        self.llm = llm
        self._usage: dict[uuid.UUID, LLMUsage] = {}

    def _record(self, req: AgentTaskRequest, u: LLMUsage) -> None:
        self._usage.setdefault(req.execution_id, LLMUsage(model=u.model)).add(u)

    def _pop_usage(self, request: AgentTaskRequest) -> tuple[TokenUsage, str | None]:
        u = self._usage.pop(request.execution_id, None)
        if u is None:
            return TokenUsage(), None
        return TokenUsage(
            input_tokens=u.input_tokens,
            output_tokens=u.output_tokens,
            cost_usd_estimate=u.cost_usd_estimate,
        ), u.model

    async def _dispatch(
        self, req: AgentTaskRequest, tool: Any
    ) -> tuple[dict[str, Any], list[Evidence]]:
        match req.task_type:
            case TaskType.CLASSIFY:
                return await self._llm_classify(req, tool)
            case TaskType.PLAN:
                return await self._llm_plan(req)
            case TaskType.REPORT:
                return await self._llm_report(req)
            case _:
                return await super()._dispatch(req, tool)

    async def _llm_classify(
        self, req: AgentTaskRequest, tool: Any
    ) -> tuple[dict[str, Any], list[Evidence]]:
        partners = await tool("crm.search_partners")
        by_name = {p["name"]: p for p in partners}
        attachments = "\n".join(
            f"[{a.get('name')}] {a.get('content', '')}" for a in req.input.get("attachments", [])
        )
        prompt = (
            f"CANDIDATE PARTNERS: {json.dumps(sorted(by_name))}\n\n"
            f"REQUEST (priority {req.input.get('priority', 'normal')}):\n"
            f"{untrusted(req.input['description'])}\n\nATTACHMENTS:\n{untrusted(attachments)}"
        )
        cls, usage = await structured(self.llm, load_prompt("intake"), prompt, Classification)
        self._record(req, usage)
        # Platform guard: the partner id is resolved from the CRM, never invented by the model.
        partner = by_name.get(cls.partner_name or "")
        if partner is None and cls.partner_name:
            cls.missing_info = sorted({*cls.missing_info, "partner_name"})
            cls.partner_name = None
        cls.partner_id = partner["id"] if partner else None
        evidence = (
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
        return cls.model_dump(), evidence

    async def _llm_plan(self, req: AgentTaskRequest) -> tuple[dict[str, Any], list[Evidence]]:
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
        plan, usage = await structured(self.llm, load_prompt("planning"), prompt, ActionPlan)
        self._record(req, usage)
        return plan.model_dump(), []

    async def _llm_report(self, req: AgentTaskRequest) -> tuple[dict[str, Any], list[Evidence]]:
        inp = req.input
        prompt = (
            f"PARTNER: {inp.get('partner_name')}\nOUTCOME: {inp['outcome']}\n"
            f"REASON: {inp.get('reason')}\nEXECUTION RECORDS: {json.dumps(inp['actions'])}"
        )
        report, usage = await structured(self.llm, load_prompt("reporting"), prompt, Report)
        self._record(req, usage)
        report.outcome = RequestStatus(inp["outcome"])  # the platform decides the outcome
        return report.model_dump(), []
