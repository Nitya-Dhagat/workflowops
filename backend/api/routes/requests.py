from __future__ import annotations

import hashlib
import uuid

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, or_, select

from backend.api.auth import CurrentUser
from backend.api.deps import SessionDep
from backend.database.models import OutboxEvent, Request
from backend.domain.enums import RequestStatus
from backend.domain.schemas import RequestCreate, RequestRead

router = APIRouter(prefix="/requests", tags=["requests"])


def dedupe_hash(description: str, requester_id: str) -> str:
    norm = " ".join(description.lower().split())
    return hashlib.sha256(f"{requester_id}|{norm}".encode()).hexdigest()


@router.post("", response_model=RequestRead, status_code=201)
async def create_request(body: RequestCreate, session: SessionDep, user: CurrentUser) -> Request:
    req = Request(
        description=body.description,
        requester_id=user.id,
        department=body.department or user.department,
        category=body.category,
        priority=body.priority,
        attachments=[a.model_dump() for a in body.attachments],
        status=RequestStatus.RECEIVED,
        dedupe_hash=dedupe_hash(body.description, user.id),
    )
    session.add(req)
    await session.flush()
    session.add(
        OutboxEvent(
            aggregate_type="request",
            aggregate_id=str(req.id),
            event_type="request.received",
            payload={"status": "received"},
        )
    )
    await session.commit()
    await session.refresh(req)
    return req


@router.get("", response_model=dict[str, object])
async def list_requests(
    session: SessionDep,
    status: RequestStatus | None = None,
    q: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict[str, object]:
    stmt = select(Request)
    if status:
        stmt = stmt.where(Request.status == status)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(Request.description.ilike(like), Request.requester_id == q))
    total = await session.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = await session.scalars(
        stmt.order_by(Request.created_at.desc()).limit(limit).offset(offset)
    )
    return {"total": total, "items": [RequestRead.model_validate(r) for r in rows]}


@router.get("/{request_id}", response_model=RequestRead)
async def get_request(request_id: uuid.UUID, session: SessionDep) -> Request:
    req = await session.get(Request, request_id)
    if req is None:
        raise HTTPException(404, "request not found")
    return req
