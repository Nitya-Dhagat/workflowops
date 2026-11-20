"""Human approval decisions: attributed, hash-bound, immutable once made."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from backend.database.models import Approval, OutboxEvent, ProposedActionRecord, User, utcnow
from backend.domain.enums import (
    ActionStatus,
    ApprovalDecisionType,
    ApprovalStatus,
    UserRole,
)
from backend.domain.schemas import ApprovalDecisionIn
from backend.policy.catalog import action_hash


class ApprovalError(Exception):
    status_code = 400


class NotFoundError(ApprovalError):
    status_code = 404


class ForbiddenError(ApprovalError):
    status_code = 403


class ConflictError(ApprovalError):
    status_code = 409


_DECISION_MAP = {
    ApprovalDecisionType.APPROVE: (ApprovalStatus.APPROVED, ActionStatus.APPROVED),
    ApprovalDecisionType.REJECT: (ApprovalStatus.REJECTED, ActionStatus.REJECTED),
    ApprovalDecisionType.REQUEST_CHANGES: (
        ApprovalStatus.CHANGES_REQUESTED,
        ActionStatus.CHANGES_REQUESTED,
    ),
}


def _aware(dt: datetime) -> datetime:

    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


async def decide(
    session: AsyncSession, approval_id: uuid.UUID, body: ApprovalDecisionIn
) -> Approval:
    approval = await session.get(Approval, approval_id, with_for_update=True)
    if approval is None:
        raise NotFoundError("approval not found")
    reviewer = await session.get(User, body.reviewer_id)
    if reviewer is None or reviewer.role != UserRole.REVIEWER or not reviewer.active:
        raise ForbiddenError("only active reviewers may decide approvals")
    if approval.status != ApprovalStatus.PENDING:
        raise ConflictError(f"approval already {approval.status}; decisions are immutable")
    now = utcnow()
    if _aware(approval.expires_at) <= now:
        raise ConflictError("approval has expired")
    action = await session.get(ProposedActionRecord, approval.action_id)
    assert action is not None
    current_hash = action_hash(action.action_type, action.params)
    if body.action_hash != approval.action_hash or current_hash != approval.action_hash:
        raise ConflictError("action changed since it was shown to the reviewer; reload")

    approval_status, action_status = _DECISION_MAP[body.decision]
    approval.status = approval_status
    approval.decided_by = reviewer.id
    approval.decided_at = now
    approval.comment = body.comment
    action.status = action_status
    session.add(
        OutboxEvent(
            aggregate_type="approval",
            aggregate_id=str(approval.id),
            event_type="approval.decided",
            payload={
                "decision": body.decision,
                "by": reviewer.id,
                "hash": approval.action_hash,
                "run_id": str(approval.run_id),
            },
        )
    )
    await session.commit()
    return approval
