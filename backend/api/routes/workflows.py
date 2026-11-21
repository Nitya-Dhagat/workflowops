from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi import Request as FastAPIRequest
from sqlalchemy import select

from backend.api.deps import EngineDep, SessionDep, spawn
from backend.database.models import (
    AgentEvent,
    AgentExecution,
    Approval,
    EvidenceRecord,
    ProposedActionRecord,
    Request,
    ToolInvocation,
    WorkflowRun,
)
from backend.domain.schemas import (
    ApprovalRead,
    EventRead,
    ProposedActionRead,
    WorkflowRunCreate,
    WorkflowRunRead,
)

router = APIRouter(prefix="/workflows", tags=["workflows"])


@router.post("/{request_id}/run", status_code=202)
async def start_run(
    request_id: uuid.UUID,
    body: WorkflowRunCreate,
    engine: EngineDep,
    http: FastAPIRequest,
    wait: bool = Query(default=False),
) -> dict[str, Any]:
    """Start the ``partner_onboarding`` workflow for a request. ``wait=true`` runs inline."""
    try:
        run_id = await engine.create_run(request_id, body.framework, body.mode)
    except LookupError as e:
        raise HTTPException(404, str(e)) from e
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    if wait:
        status = await engine.advance(run_id)
        return {"run_id": str(run_id), "status": status}
    spawn(http, engine.advance(run_id))
    return {"run_id": str(run_id), "status": "started"}


@router.get("/runs", response_model=list[WorkflowRunRead])
async def list_runs(session: SessionDep, request_id: uuid.UUID | None = None) -> list[Any]:
    stmt = select(WorkflowRun).order_by(WorkflowRun.created_at.desc()).limit(100)
    if request_id:
        stmt = stmt.where(WorkflowRun.request_id == request_id)
    return list(await session.scalars(stmt))


@router.get("/runs/{run_id}")
async def get_run(run_id: uuid.UUID, session: SessionDep) -> dict[str, Any]:
    run = await session.get(WorkflowRun, run_id)
    if run is None:
        raise HTTPException(404, "run not found")
    req = await session.get(Request, run.request_id)
    actions = await session.scalars(
        select(ProposedActionRecord)
        .where(ProposedActionRecord.run_id == run_id)
        .order_by(ProposedActionRecord.key)
    )
    approvals = await session.scalars(select(Approval).where(Approval.run_id == run_id))
    execs = await session.scalars(
        select(AgentExecution)
        .where(AgentExecution.run_id == run_id)
        .order_by(AgentExecution.created_at)
    )
    evidence = await session.scalars(select(EvidenceRecord).where(EvidenceRecord.run_id == run_id))
    tools = await session.scalars(
        select(ToolInvocation)
        .where(ToolInvocation.run_id == run_id)
        .order_by(ToolInvocation.created_at)
    )
    return {
        "run": WorkflowRunRead.model_validate(run),
        "request_status": req.status if req else None,
        "request_status_reason": req.status_reason if req else None,
        "actions": [ProposedActionRead.model_validate(a) for a in actions],
        "approvals": [
            {
                **ApprovalRead.model_validate(a).model_dump(mode="json"),
                "action": a.action_snapshot,
                "request_description": req.description if req else None,
            }
            for a in approvals
        ],
        "agent_executions": [
            {
                "id": e.id,
                "stage": e.stage,
                "agent": e.agent,
                "framework": e.framework,
                "prompt_version": e.prompt_version,
                "status": e.status,
                "error": e.error,
                "latency_ms": e.latency_ms,
                "input_tokens": e.input_tokens,
                "output_tokens": e.output_tokens,
                "cost_usd_estimate": e.cost_usd_estimate,
            }
            for e in execs
        ],
        "evidence": [
            {"ref": e.ref, "source": e.source, "summary": e.summary, "collected_by": e.collected_by}
            for e in evidence
        ],
        "tool_invocations": [
            {
                "agent": t.agent,
                "tool": t.tool,
                "status": t.status,
                "error": t.error,
                "latency_ms": t.latency_ms,
            }
            for t in tools
        ],
    }


@router.get("/runs/{run_id}/events", response_model=list[EventRead])
async def run_events(run_id: uuid.UUID, session: SessionDep, after: int = 0) -> list[Any]:
    rows = await session.scalars(
        select(AgentEvent)
        .where(AgentEvent.run_id == run_id, AgentEvent.seq > after)
        .order_by(AgentEvent.seq)
    )
    return list(rows)


@router.post("/runs/{run_id}/cancel")
async def cancel_run(run_id: uuid.UUID, engine: EngineDep) -> dict[str, str]:
    try:
        await engine.cancel(run_id)
    except LookupError as e:
        raise HTTPException(404, str(e)) from e
    return {"status": "cancelled"}


@router.post("/runs/{run_id}/resume")
async def resume_run(
    run_id: uuid.UUID, engine: EngineDep, http: FastAPIRequest, wait: bool = Query(default=False)
) -> dict[str, str]:
    if wait:
        return {"status": await engine.advance(run_id)}
    spawn(http, engine.advance(run_id))
    return {"status": "resuming"}
