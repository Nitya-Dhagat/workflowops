"""LangGraph framework, offline: the plan graph's self-check loop repairs a bad draft."""

from __future__ import annotations

import json
import re
from typing import Any

import pytest

from backend.domain.enums import RequestStatus
from backend.frameworks.langgraph.adapter import LangGraphAdapter
from backend.llm.client import FakeLLMClient

NW = "partner-0001"
GOOD = [
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
]
BAD = [
    {
        "key": "a1",
        "action_type": "delete_partner",
        "params": {"partner_id": NW},
        "risk_class": "low",
        "evidence_refs": ["policy:MADE-UP"],
    }
]


@pytest.mark.integration
async def test_plan_graph_self_check_loop(client, api) -> None:  # type: ignore[no-untyped-def]
    plans = [BAD, GOOD]

    def respond(system: str, prompt: str) -> str:
        if "Intake Agent" in system:
            return json.dumps(
                {
                    "request_type": "partner_onboarding",
                    "in_scope": True,
                    "partner_name": "Northwind Cloud",
                    "missing_info": [],
                    "urgency": "normal",
                    "needs_human_escalation": False,
                    "rationale": "named",
                }
            )
        if "Planning Agent" in system:
            return json.dumps({"actions": plans.pop(0), "rationale": "draft"})
        outcome = re.search(r"OUTCOME: (\w+)", prompt).group(1)  # type: ignore[union-attr]
        return json.dumps({"summary": "ok", "outcome": outcome})

    fake = FakeLLMClient(respond)
    api.state.engine.adapters["langgraph"] = lambda gw: LangGraphAdapter(gw, fake)
    r = await client.post(
        "/api/v1/requests", json={"description": "Onboard Northwind Cloud", "requester_id": "u_01"}
    )
    r = await client.post(
        f"/api/v1/workflows/{r.json()['id']}/run?wait=true", json={"framework": "langgraph"}
    )
    d: dict[str, Any] = (await client.get(f"/api/v1/workflows/runs/{r.json()['run_id']}")).json()
    assert d["request_status"] == RequestStatus.AWAITING_REQUESTER_INFO, d["request_status_reason"]
    assert sum(1 for s, _ in fake.calls if "Planning Agent" in s) == 2  # draft, revise
    retry_prompt = [p for s, p in fake.calls if "Planning Agent" in s][1]
    assert "unknown action type delete_partner" in retry_prompt  # feedback reached the model
    assert "draft -> self_check:" in d["run"]["state"]["plan"]["rationale"]
    assert {a["action_type"] for a in d["actions"]} == {
        "create_onboarding_task",
        "request_compliance_doc",
    }
