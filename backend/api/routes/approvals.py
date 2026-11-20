from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi import Request as FastAPIRequest
from sqlalchemy import select

from backend.api.deps import EngineDep, SessionDep, spawn
from backend.approvals.service import ApprovalError, decide
from backend.database.models import Approval
from backend.domain.enums import ApprovalStatus
from backend.domain.schemas import ApprovalDecisionIn, ApprovalRead

router = APIRouter(prefix="/approvals", tags=["approvals"])


@router.get("")
async def list_approvals(
    session: SessionDep,
    status: ApprovalStatus | None = ApprovalStatus.PENDING,
    run_id: uuid.UUID | None = None,
) -> list[dict[str, Any]]:
    stmt = select(Approval).order_by(Approval.created_at)
    if status:
        stmt = stmt.where(Approval.status == status)
    if run_id:
        stmt = stmt.where(Approval.run_id == run_id)
    return [
        {**ApprovalRead.model_validate(a).model_dump(mode="json"), "action": a.action_snapshot}
        for a in await session.scalars(stmt)
    ]


@router.post("/{approval_id}/decision", response_model=ApprovalRead)
async def decide_approval(
    approval_id: uuid.UUID,
    body: ApprovalDecisionIn,
    session: SessionDep,
    engine: EngineDep,
    http: FastAPIRequest,
    wait: bool = Query(default=False),
) -> Approval:
    try:
        approval = await decide(session, approval_id, body)
    except ApprovalError as e:
        raise HTTPException(e.status_code, str(e)) from e
    # Resume the run; the engine stays paused if other approvals are still pending.
    if wait:
        await engine.advance(approval.run_id)
    else:
        spawn(http, engine.advance(approval.run_id))
    return approval
