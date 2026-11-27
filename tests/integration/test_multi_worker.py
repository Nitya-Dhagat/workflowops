"""Two workers advancing the same run: the database lease lets exactly one do the work."""

from __future__ import annotations

import asyncio
import random
import uuid

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import func, select

from backend.database.models import AgentExecution, WorkflowRun
from backend.domain.enums import RequestStatus
from backend.orchestration.engine import WorkflowEngine

pytestmark = pytest.mark.integration


async def _no_sleep(_: float) -> None:
    return None


def worker(api: FastAPI, name: str, claim_wait_s: float = 0.5) -> WorkflowEngine:
    return WorkflowEngine(
        api.state.sessionmaker,
        api.state.connectors,
        api.state.engine.adapters,
        api.state.settings,
        sleep=_no_sleep,
        rng=random.Random(0),
        worker_id=name,
        claim_wait_s=claim_wait_s,
    )


async def _request(client: httpx.AsyncClient) -> uuid.UUID:
    r = await client.post("/api/v1/requests", json={"description": "Onboard Northwind Cloud"})
    return uuid.UUID(r.json()["id"])


async def test_second_worker_backs_off_while_first_holds_lease(client, api, mock_client) -> None:  # type: ignore[no-untyped-def]
    a, b = worker(api, "worker-a"), worker(api, "worker-b")
    gate = asyncio.Event()

    async def slow(status: RequestStatus) -> None:
        if status is RequestStatus.INVESTIGATING:
            await gate.wait()  # hold worker A mid-run, lease claimed

    a.after_stage = slow
    run_id = await a.create_run(await _request(client))
    task_a = asyncio.create_task(a.advance(run_id))
    await asyncio.sleep(0.3)
    status_b = await b.advance(run_id)  # cannot claim: returns without doing any work
    assert status_b is RequestStatus.INVESTIGATING
    gate.set()
    assert await task_a is RequestStatus.AWAITING_REQUESTER_INFO
    async with api.state.sessionmaker() as s:
        intakes = await s.scalar(
            select(func.count())
            .select_from(AgentExecution)
            .where(AgentExecution.run_id == run_id, AgentExecution.agent == "intake")
        )
        run = await s.get(WorkflowRun, run_id)
    assert intakes == 1  # B never ran a stage
    assert run is not None and run.lease_owner is None  # released when the run paused
    tickets = [
        t
        for t in (await mock_client.get("/_control/state")).json()["tickets"].values()
        if t.get("idempotency_key")
    ]
    assert len(tickets) == 1


async def test_lease_is_released_on_pause_so_another_worker_can_resume(client, api) -> None:  # type: ignore[no-untyped-def]
    r = await client.post("/api/v1/requests", json={"description": "Onboard Juniper Logic"})
    a, b = worker(api, "worker-a"), worker(api, "worker-b")
    run_id = await a.create_run(uuid.UUID(r.json()["id"]))
    assert await a.advance(run_id) is RequestStatus.AWAITING_APPROVAL
    # B can pick the paused run up immediately (no waiting for A's lease to expire).
    assert await b._claim(run_id)
