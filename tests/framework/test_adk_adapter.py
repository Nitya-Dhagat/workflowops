"""Google ADK framework, run offline with a scripted ADK model (no network)."""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from typing import Any

import pytest
from google.adk.models import BaseLlm, LlmRequest, LlmResponse
from google.genai import types
from pydantic import Field

from backend.domain.enums import RequestStatus
from backend.frameworks.adk.adapter import ADKAdapter

NW = "partner-0001"
PLAN = [
    {
        "key": "a1",
        "action_type": "create_onboarding_task",
        "params": {"partner_id": NW, "owner_id": "u_20", "title": "Onboard Northwind Cloud"},
        "risk_class": "medium",
        "evidence_refs": [f"crm:{NW}"],
    },
    {
        "key": "a2",
        "action_type": "request_compliance_doc",
        "params": {"partner_id": NW, "doc_type": "SOC2_TYPE_II", "requester_id": "u_01"},
        "risk_class": "medium",
        "evidence_refs": [f"crm:{NW}"],
    },
    {
        "key": "a3",
        "action_type": "grant_portal_access",
        "params": {"partner_id": NW, "access_level": "admin"},
        "risk_class": "low",
        "evidence_refs": [f"crm:{NW}"],
    },
]


class ScriptedLlm(BaseLlm):
    """Calls the search tool once, then answers; plans and critiques with fixed JSON."""

    model: str = "fake-adk"
    seen: list[str] = Field(default_factory=list)

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        system = str(llm_request.config.system_instruction or "")
        last = llm_request.contents[-1].parts or []
        has_tool_result = any(p.function_response for p in last)
        self.seen.append(system[:40])
        if "Intake Agent" in system and not has_tool_result:
            part = types.Part(
                function_call=types.FunctionCall(
                    name="search_partners", args={"query": "Northwind"}
                )
            )
        elif "Intake Agent" in system:
            part = types.Part(
                text=json.dumps(
                    {
                        "request_type": "partner_onboarding",
                        "in_scope": True,
                        "partner_name": "Northwind Cloud",
                        "missing_info": [],
                        "urgency": "normal",
                        "needs_human_escalation": False,
                        "rationale": "found via tool",
                    }
                )
            )
        elif "Plan Critic" in system:
            part = types.Part(text=json.dumps({"actions": PLAN, "rationale": "critiqued"}))
        elif "Planning Agent" in system:
            part = types.Part(text=json.dumps({"actions": PLAN, "rationale": "draft"}))
        else:
            part = types.Part(text=json.dumps({"summary": "Scripted.", "outcome": "x"}))
        yield LlmResponse(
            content=types.Content(role="model", parts=[part]),
            usage_metadata=types.GenerateContentResponseUsageMetadata(
                prompt_token_count=100, candidates_token_count=20
            ),
        )


@pytest.mark.integration
async def test_adk_pipeline_with_tool_call_and_critic(client, api, mock_client) -> None:  # type: ignore[no-untyped-def]
    llm = ScriptedLlm()
    api.state.engine.adapters["adk"] = lambda gw: ADKAdapter(gw, llm)
    r = await client.post("/api/v1/requests", json={"description": "Onboard Northwind Cloud"})
    r = await client.post(
        f"/api/v1/workflows/{r.json()['id']}/run?wait=true", json={"framework": "adk"}
    )
    d: dict[str, Any] = (await client.get(f"/api/v1/workflows/runs/{r.json()['run_id']}")).json()
    assert d["request_status"] == RequestStatus.AWAITING_REQUESTER_INFO, d["request_status_reason"]
    status = {a["action_type"]: a["status"] for a in d["actions"]}
    assert status["grant_portal_access"] == "rejected_by_validation"  # policy engine wins
    assert status["create_onboarding_task"] == "verified"
    # The ADK intake agent's tool call went through the gateway and was audited.
    assert any(
        t["agent"] == "intake" and t["tool"] == "crm.search_partners" for t in d["tool_invocations"]
    )
    assert any(s.startswith("You are the Plan Critic") for s in llm.seen)
    execs = {e["agent"]: e for e in d["agent_executions"]}
    assert execs["planning"]["input_tokens"] == 200  # planner + critic turns
    assert execs["planning"]["framework"] == "adk"
