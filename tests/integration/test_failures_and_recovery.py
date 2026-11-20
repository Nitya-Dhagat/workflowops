"""Retries, ambiguous writes, and crash recovery without duplicate side effects."""

from __future__ import annotations

import random
import uuid
from datetime import timedelta
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select, update

from backend.database.models import ActionExecution, ProposedActionRecord, WorkflowRun, utcnow
from backend.domain.enums import RequestStatus
from backend.orchestration import executor as executor_mod
from backend.orchestration.engine import WorkflowEngine

pytestmark = pytest.mark.integration
NORTHWIND = "Onboard Northwind Cloud as a partner"


async def _no_sleep(_: float) -> None:
    return None


async def new_request(client: httpx.AsyncClient, text: str = NORTHWIND) -> uuid.UUID:
    r = await client.post("/api/v1/requests", json={"description": text, "requester_id": "u_01"})
    return uuid.UUID(r.json()["id"])


async def created_tickets(mock_client: httpx.AsyncClient) -> list[dict[str, Any]]:
    st = (await mock_client.get("/_control/state")).json()
    return [t for t in st["tickets"].values() if t.get("idempotency_key")]


async def set_faults(mock_client: httpx.AsyncClient, rules: list[dict[str, Any]]) -> None:
    await mock_client.post("/_control/faults", json=rules)


async def test_transient_5xx_is_retried(client, api: FastAPI, mock_client) -> None:  # type: ignore[no-untyped-def]
    await set_faults(
        mock_client,
        [{"fault": "error_500", "path_prefix": "/ticketing/tickets", "method": "POST", "times": 2}],
    )
    engine: WorkflowEngine = api.state.engine
    run_id = await engine.create_run(await new_request(client))
    assert await engine.advance(run_id) is RequestStatus.AWAITING_REQUESTER_INFO
    assert len(await created_tickets(mock_client)) == 1
    async with api.state.sessionmaker() as s:
        task = await s.scalar(
            select(ProposedActionRecord).where(
                ProposedActionRecord.run_id == run_id,
                ProposedActionRecord.action_type == "create_onboarding_task",
            )
        )
        attempts = list(
            await s.scalars(
                select(ActionExecution)
                .where(ActionExecution.action_id == task.id)
                .order_by(ActionExecution.attempt)
            )
        )  # type: ignore[union-attr]
    assert [a.status for a in attempts] == ["retryable_error", "retryable_error", "succeeded"]


async def test_lost_ack_without_server_idempotency_does_not_duplicate(
    client, api, mock_client
) -> None:  # type: ignore[no-untyped-def]
    # Server ignores Idempotency-Key, so only platform read-back prevents a duplicate.
    await mock_client.post("/_control/config", json={"honor_idempotency": False})
    await set_faults(
        mock_client,
        [{"fault": "ack_lost", "path_prefix": "/ticketing/tickets", "method": "POST", "times": 1}],
    )
    engine: WorkflowEngine = api.state.engine
    run_id = await engine.create_run(await new_request(client))
    await engine.advance(run_id)
    tickets = await created_tickets(mock_client)
    assert len(tickets) == 1
    async with api.state.sessionmaker() as s:
        task = await s.scalar(
            select(ProposedActionRecord).where(
                ProposedActionRecord.run_id == run_id,
                ProposedActionRecord.action_type == "create_onboarding_task",
            )
        )
    assert task is not None and task.status == "verified"
    assert task.result["recovered_by_readback"] is True  # type: ignore[index]


async def test_permanent_error_fails_action_without_retry(client, api, mock_client) -> None:  # type: ignore[no-untyped-def]
    await set_faults(
        mock_client,
        [{"fault": "error_400", "path_prefix": "/notifications", "method": "POST", "times": -1}],
    )
    engine: WorkflowEngine = api.state.engine
    run_id = await engine.create_run(await new_request(client))
    status = await engine.advance(run_id)
    assert status is RequestStatus.COMPLETED_WITH_WARNINGS  # task ok, doc request failed
    async with api.state.sessionmaker() as s:
        doc = await s.scalar(
            select(ProposedActionRecord).where(
                ProposedActionRecord.run_id == run_id,
                ProposedActionRecord.action_type == "request_compliance_doc",
            )
        )
    assert doc is not None and doc.status == "failed" and doc.result["attempts"] == 1  # type: ignore[index]


async def test_retries_are_bounded(client, api, mock_client) -> None:  # type: ignore[no-untyped-def]
    await set_faults(
        mock_client,
        [
            {
                "fault": "error_500",
                "path_prefix": "/ticketing/tickets",
                "method": "POST",
                "times": -1,
            }
        ],
    )
    engine: WorkflowEngine = api.state.engine
    run_id = await engine.create_run(await new_request(client))
    await engine.advance(run_id)
    async with api.state.sessionmaker() as s:
        task = await s.scalar(
            select(ProposedActionRecord).where(
                ProposedActionRecord.run_id == run_id,
                ProposedActionRecord.action_type == "create_onboarding_task",
            )
        )
        n = len(
            list(
                await s.scalars(select(ActionExecution).where(ActionExecution.action_id == task.id))
            )
        )  # type: ignore[union-attr]
    assert n == api.state.settings.max_action_attempts
    assert task.status == "failed"  # type: ignore[union-attr]


class Crash(Exception):
    pass


async def _recover_with_fresh_worker(api: FastAPI, run_id: uuid.UUID) -> RequestStatus:
    async with api.state.sessionmaker() as s:  # simulate the dead worker's lease expiring
        await s.execute(update(WorkflowRun).values(lease_expires_at=utcnow() - timedelta(1)))
        await s.commit()
    fresh = WorkflowEngine(
        api.state.sessionmaker,
        api.state.connectors,
        api.state.engine.adapters,
        api.state.settings,
        sleep=_no_sleep,
        rng=random.Random(1),
    )
    assert await fresh.recover() == [run_id]
    async with api.state.sessionmaker() as s:
        run = await s.get(WorkflowRun, run_id)
        assert run is not None
        return RequestStatus((run.state.get("report") or {})["outcome"])


async def test_crash_between_actions_recovers_without_duplicates(client, api, mock_client) -> None:  # type: ignore[no-untyped-def]
    await mock_client.post("/_control/config", json={"honor_idempotency": False})
    engine: WorkflowEngine = api.state.engine
    calls = {"n": 0}

    async def hook(status: RequestStatus) -> None:
        if status is RequestStatus.EXECUTING:
            calls["n"] += 1  # 1 = entered EXECUTING, 2 = first action committed
            if calls["n"] == 2:
                raise Crash("worker killed after first action")

    engine.after_stage = hook
    run_id = await engine.create_run(await new_request(client))
    with pytest.raises(Crash):
        await engine.advance(run_id)
    assert len(await created_tickets(mock_client)) == 1
    assert await _recover_with_fresh_worker(api, run_id) is RequestStatus.AWAITING_REQUESTER_INFO
    st = (await mock_client.get("/_control/state")).json()
    assert len(await created_tickets(mock_client)) == 1
    assert len(st["notifications"]) == 1


async def test_crash_after_side_effect_before_commit(client, api, mock_client, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Worst case: the external write happened but the platform never recorded success."""
    await mock_client.post("/_control/config", json={"honor_idempotency": False})
    original = executor_mod.ActionExecutor._succeed
    state = {"crashed": False}

    async def crashing_succeed(self, session, action, *a, **kw):  # type: ignore[no-untyped-def]
        if not state["crashed"]:
            state["crashed"] = True
            raise Crash("died before recording success")
        return await original(self, session, action, *a, **kw)

    monkeypatch.setattr(executor_mod.ActionExecutor, "_succeed", crashing_succeed)
    engine: WorkflowEngine = api.state.engine
    run_id = await engine.create_run(await new_request(client))
    with pytest.raises(Crash):
        await engine.advance(run_id)
    assert len(await created_tickets(mock_client)) == 1
    assert await _recover_with_fresh_worker(api, run_id) is RequestStatus.AWAITING_REQUESTER_INFO
    assert len(await created_tickets(mock_client)) == 1  # no duplicate side effect


async def test_mock_systems_down_fails_cleanly(client, api, mock_client) -> None:  # type: ignore[no-untyped-def]
    await set_faults(mock_client, [{"fault": "error_500", "path_prefix": "/crm", "times": -1}])
    engine: WorkflowEngine = api.state.engine
    run_id = await engine.create_run(await new_request(client))
    assert await engine.advance(run_id) is RequestStatus.FAILED
