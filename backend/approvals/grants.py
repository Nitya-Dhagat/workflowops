"""Execution grants: proof, minted from database state, that an action may run.

The gateway no longer trusts a status passed by its caller. Before a side effect, the executor
asks for a grant; ``issue_grant`` re-reads the action and its approval from the database and
refuses unless the action is approved *and*, for actions that need a human, an approval row
decided by a reviewer matches the action's current hash. The grant is HMAC-signed and short-lived;
the gateway verifies the signature, the age, the action hash and the idempotency key.
"""

from __future__ import annotations

import hashlib
import hmac
import time
import uuid

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database.models import Approval, ProposedActionRecord
from backend.domain.enums import ActionStatus, ApprovalStatus, RiskClass
from backend.policy.catalog import CATALOG, action_hash

GRANT_TTL_S = 600


class UnauthorizedExecutionError(Exception):
    pass


class ExecutionGrant(BaseModel):
    action_id: uuid.UUID
    action_type: str
    payload_hash: str
    idempotency_key: str
    issued_at: float
    signature: str = ""

    def message(self) -> bytes:
        return "|".join(
            [
                str(self.action_id),
                self.action_type,
                self.payload_hash,
                self.idempotency_key,
                f"{self.issued_at:.6f}",
            ]
        ).encode()


def sign(grant: ExecutionGrant, secret: bytes) -> ExecutionGrant:
    sig = hmac.new(secret, grant.message(), hashlib.sha256).hexdigest()
    return grant.model_copy(update={"signature": sig})


def needs_human(action_type: str, medium_requires_approval: bool) -> bool:
    risk = CATALOG[action_type].risk
    return risk is RiskClass.HIGH or (risk is RiskClass.MEDIUM and medium_requires_approval)


async def issue_grant(
    session: AsyncSession,
    action_id: uuid.UUID,
    secret: bytes,
    *,
    medium_requires_approval: bool = False,
) -> ExecutionGrant:
    action = await session.get(ProposedActionRecord, action_id, populate_existing=True)
    if action is None or action.action_type not in CATALOG:
        raise UnauthorizedExecutionError("unknown action")
    if action.status not in {ActionStatus.APPROVED, ActionStatus.EXECUTING}:
        raise UnauthorizedExecutionError(f"action not approved (status={action.status})")
    current = action_hash(action.action_type, action.params)
    if current != action.payload_hash:
        raise UnauthorizedExecutionError("action changed after validation; re-approval needed")
    if needs_human(action.action_type, medium_requires_approval):
        approval = await session.scalar(
            select(Approval).where(
                Approval.action_id == action.id, Approval.status == ApprovalStatus.APPROVED
            )
        )
        if approval is None or approval.decided_by is None:
            raise UnauthorizedExecutionError("no recorded human approval for this action")
        if approval.action_hash != current:
            raise UnauthorizedExecutionError("approval was for a different version of the action")
    return sign(
        ExecutionGrant(
            action_id=action.id,
            action_type=action.action_type,
            payload_hash=current,
            idempotency_key=action.idempotency_key,
            issued_at=time.time(),
        ),
        secret,
    )


def verify_grant(
    grant: ExecutionGrant,
    action_type: str,
    params: dict[str, object],
    idempotency_key: str,
    secret: bytes,
    *,
    now: float | None = None,
) -> None:
    expected = sign(grant, secret).signature
    if not hmac.compare_digest(expected, grant.signature):
        raise UnauthorizedExecutionError("invalid grant signature")
    if (now or time.time()) - grant.issued_at > GRANT_TTL_S:
        raise UnauthorizedExecutionError("grant expired")
    if grant.action_type != action_type or grant.idempotency_key != idempotency_key:
        raise UnauthorizedExecutionError("grant does not match this action")
    if action_hash(action_type, dict(params)) != grant.payload_hash:
        raise UnauthorizedExecutionError("action changed after the grant was issued")
