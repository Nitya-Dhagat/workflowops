"""Executes approved actions exactly once per idempotency key, with bounded retries."""

from __future__ import annotations

import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.logging import get_logger, redact
from backend.database.models import ActionExecution, IdempotencyKey, ProposedActionRecord, utcnow
from backend.domain.enums import ActionStatus
from backend.integrations.base import ConnectorError
from backend.orchestration.bindings import connector_params, external_ref, locate
from backend.tools.gateway import ToolGateway, UnauthorizedExecutionError

log = get_logger(__name__)
Sleep = Callable[[float], Awaitable[None]]


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 4
    base_delay_s: float = 0.2
    max_delay_s: float = 3.0

    def delay(self, attempt: int, rng: random.Random) -> float:
        """Exponential backoff with 'equal jitter' (half fixed, half random)."""
        d = min(self.max_delay_s, self.base_delay_s * (2 ** (attempt - 1)))
        return float(d / 2 + rng.uniform(0, d / 2))


class ActionExecutor:
    def __init__(
        self,
        gateway: ToolGateway,
        retry: RetryPolicy,
        sleep: Sleep,
        rng: random.Random | None = None,
    ) -> None:
        self.gateway = gateway
        self.retry = retry
        self.sleep = sleep
        self.rng = rng or random.Random()

    async def run(self, session: AsyncSession, action: ProposedActionRecord) -> None:
        """Run one action to a final SUCCEEDED/FAILED state, committing as it goes."""
        key = action.idempotency_key
        existing = await session.get(IdempotencyKey, key)
        if existing and existing.status == "completed":
            action.status = ActionStatus.SUCCEEDED
            action.result = {
                **(action.result or {}),
                "external_ref": existing.external_ref,
                "deduplicated": True,
            }
            await session.commit()
            return
        if existing is None:
            session.add(IdempotencyKey(key=key, action_id=action.id, status="in_progress"))
        else:
            # A previous attempt was in flight when the worker died: read back first.
            ref = await self._safe_locate(action)
            if ref:
                await self._succeed(session, action, existing, ref, recovered=True)
                return
        action.status = ActionStatus.EXECUTING
        await session.commit()
        idem = existing or await session.get(IdempotencyKey, key)
        assert idem is not None

        cparams = connector_params(action.action_type, action.params)
        for attempt in range(1, self.retry.max_attempts + 1):
            t0 = time.perf_counter()
            try:
                res = await self.gateway.execute_action(
                    action_type=action.action_type,
                    params=action.params,
                    status=ActionStatus(action.status),
                    approved_hash=action.payload_hash,
                    idempotency_key=key,
                    connector_params=cparams,
                    run_id=action.run_id,
                )
            except UnauthorizedExecutionError as e:
                await self._fail(session, action, attempt, cparams, str(e), t0)
                return
            except ConnectorError as e:
                self._log_attempt(
                    session,
                    action,
                    attempt,
                    cparams,
                    None,
                    str(e),
                    t0,
                    "retryable_error" if e.retryable else "failed",
                )
                await session.commit()
                if e.ambiguous:
                    ref = await self._safe_locate(action)
                    if ref:
                        await self._succeed(session, action, idem, ref, recovered=True)
                        return
                if not e.retryable or attempt == self.retry.max_attempts:
                    await self._fail(session, action, attempt, cparams, str(e), t0, log_it=False)
                    return
                await self.sleep(self.retry.delay(attempt, self.rng))
                continue
            ref = external_ref(action.action_type, res.data)
            self._log_attempt(session, action, attempt, cparams, res.data, None, t0, "succeeded")
            await self._succeed(session, action, idem, ref, replayed=res.replayed)
            return

    async def _safe_locate(self, action: ProposedActionRecord) -> str | None:
        try:
            return await locate(
                self.gateway, action.action_type, action.params, action.idempotency_key
            )
        except ConnectorError:
            return None

    def _log_attempt(
        self,
        session: AsyncSession,
        action: ProposedActionRecord,
        attempt: int,
        req: dict[str, Any],
        resp: Any,
        err: str | None,
        t0: float,
        status: str,
    ) -> None:
        session.add(
            ActionExecution(
                action_id=action.id,
                attempt=attempt,
                status=status,
                idempotency_key=action.idempotency_key,
                request_redacted=redact(req),
                response_redacted=redact(resp) if isinstance(resp, dict) else None,
                error=err,
                latency_ms=(time.perf_counter() - t0) * 1000,
            )
        )

    async def _succeed(
        self,
        session: AsyncSession,
        action: ProposedActionRecord,
        idem: IdempotencyKey,
        ref: str | None,
        *,
        recovered: bool = False,
        replayed: bool = False,
    ) -> None:
        idem.status = "completed"
        idem.external_ref = ref
        idem.completed_at = utcnow()
        action.status = ActionStatus.SUCCEEDED
        action.result = {
            "external_ref": ref,
            "recovered_by_readback": recovered,
            "server_replayed": replayed,
        }
        await session.commit()
        log.info("action_succeeded", action=action.key, type=action.action_type, ref=ref)

    async def _fail(
        self,
        session: AsyncSession,
        action: ProposedActionRecord,
        attempt: int,
        req: dict[str, Any],
        err: str,
        t0: float,
        *,
        log_it: bool = True,
    ) -> None:
        if log_it:
            self._log_attempt(session, action, attempt, req, None, err, t0, "failed")
        action.status = ActionStatus.FAILED
        action.result = {"error": err, "attempts": attempt}
        await session.commit()
        log.warning("action_failed", action=action.key, type=action.action_type, error=err)


async def executions_for(session: AsyncSession, action_id: Any) -> list[ActionExecution]:
    rows = await session.scalars(
        select(ActionExecution)
        .where(ActionExecution.action_id == action_id)
        .order_by(ActionExecution.attempt)
    )
    return list(rows)
