"""The executor/gateway trust database state, not caller-supplied status.

Each test tampers with something an attacker or a bug could touch and checks that no side
effect reaches the business systems.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select

from backend.approvals.grants import (
    ExecutionGrant,
    UnauthorizedExecutionError,
    issue_grant,
    sign,
    verify_grant,
)
from backend.database.models import ProposedActionRecord
from backend.domain.enums import ActionStatus
from backend.orchestration.executor import ActionExecutor, RetryPolicy
from backend.policy.catalog import action_hash
from backend.tools.gateway import ToolGateway

pytestmark = pytest.mark.integration
SECRET = b"test-secret"
PARAMS = {"partner_id": "partner-0011", "access_level": "standard"}


async def _no_sleep(_: float) -> None:
    return None


async def _paused_juniper(client: httpx.AsyncClient) -> str:
    r = await client.post(
        "/api/v1/requests", json={"description": "Onboard Juniper Logic", "requester_id": "u_01"}
    )
    r = await client.post(
        f"/api/v1/workflows/{r.json()['id']}/run?wait=true", json={"framework": "rules"}
    )
    assert r.json()["status"] == "awaiting_approval"
    return str(r.json()["run_id"])


async def _access_action(api: FastAPI, run_id: str) -> Any:
    async with api.state.sessionmaker() as s:
        return await s.scalar(
            select(ProposedActionRecord).where(
                ProposedActionRecord.run_id == uuid.UUID(run_id),
                ProposedActionRecord.action_type == "grant_portal_access",
            )
        )


async def _execute(api: FastAPI, action_id: uuid.UUID, mutate: Any) -> ProposedActionRecord:
    gw = ToolGateway(api.state.connectors, grant_secret=SECRET)
    ex = ActionExecutor(gw, RetryPolicy(), _no_sleep, secret=SECRET)
    async with api.state.sessionmaker() as s:
        action = await s.get(ProposedActionRecord, action_id)
        assert action is not None
        mutate(action)
        await s.commit()
        await ex.run(s, action)
        return action


async def _writes(mock_client: httpx.AsyncClient) -> int:
    return int((await mock_client.get("/_control/state")).json()["write_count"])


async def test_status_flip_without_human_approval_is_refused(client, api, mock_client) -> None:  # type: ignore[no-untyped-def]
    action = await _access_action(api, await _paused_juniper(client))

    def flip(a: ProposedActionRecord) -> None:
        a.status = ActionStatus.APPROVED  # bypasses the approvals service entirely

    done = await _execute(api, action.id, flip)
    assert done.status == ActionStatus.FAILED
    assert "no recorded human approval" in done.result["error"]
    assert await _writes(mock_client) == 0


async def test_params_changed_after_approval_are_refused(client, api, mock_client) -> None:  # type: ignore[no-untyped-def]
    run_id = await _paused_juniper(client)
    ap = next(
        a
        for a in (await client.get(f"/api/v1/workflows/runs/{run_id}")).json()["approvals"]
        if a["action"]["action_type"] == "grant_portal_access"
    )
    r = await client.post(
        f"/api/v1/approvals/{ap['id']}/decision",
        json={"decision": "approve", "reviewer_id": "u_10", "action_hash": ap["action_hash"]},
    )
    assert r.status_code == 200
    action = await _access_action(api, run_id)
    admin = {**action.params, "access_level": "admin"}

    def escalate(a: ProposedActionRecord) -> None:  # even with a recomputed payload hash
        a.params = admin
        a.payload_hash = action_hash(a.action_type, admin)

    done = await _execute(api, action.id, escalate)
    assert done.status == ActionStatus.FAILED
    assert "different version" in done.result["error"]
    assert await _writes(mock_client) == 0


def _grant(**over: Any) -> ExecutionGrant:
    base = {
        "action_id": uuid.uuid4(),
        "action_type": "grant_portal_access",
        "payload_hash": action_hash("grant_portal_access", PARAMS),
        "idempotency_key": "wfo-k",
        "issued_at": time.time(),
    }
    return sign(ExecutionGrant(**{**base, **over}), SECRET)


def test_gateway_accepts_only_genuine_matching_grants() -> None:
    g = _grant()
    verify_grant(g, "grant_portal_access", PARAMS, "wfo-k", SECRET)  # genuine: ok
    cases = [
        (g.model_copy(update={"signature": "0" * 64}), PARAMS, "wfo-k", "signature"),
        (sign(g, b"other-secret"), PARAMS, "wfo-k", "signature"),
        (_grant(issued_at=time.time() - 3600), PARAMS, "wfo-k", "expired"),
        (g, {**PARAMS, "access_level": "admin"}, "wfo-k", "changed"),
        (g, PARAMS, "wfo-other", "does not match"),
    ]
    for grant, params, key, msg in cases:
        with pytest.raises(UnauthorizedExecutionError, match=msg):
            verify_grant(grant, "grant_portal_access", params, key, SECRET)


async def test_gateway_without_secret_executes_nothing(api) -> None:  # type: ignore[no-untyped-def]
    gw = ToolGateway(api.state.connectors)
    with pytest.raises(UnauthorizedExecutionError, match="no grant secret"):
        await gw.execute_action(
            action_type="grant_portal_access",
            params=PARAMS,
            grant=_grant(),
            idempotency_key="wfo-k",
            connector_params=PARAMS,
        )


async def test_issue_grant_refuses_unknown_action(api) -> None:  # type: ignore[no-untyped-def]
    async with api.state.sessionmaker() as s:
        with pytest.raises(UnauthorizedExecutionError, match="unknown action"):
            await issue_grant(s, uuid.uuid4(), SECRET)
