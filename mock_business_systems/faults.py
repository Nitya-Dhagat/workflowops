"""Configurable fault injection: timeouts, 5xx, malformed JSON, slowness, lost acks.

Faults are configured either per-request with the ``X-Fault`` header, or globally via
``POST /_control/faults``. ``ack_lost`` applies the side effect and then returns 503, which
simulates a duplicate-delivery hazard: a naive client retry would double-apply.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Literal

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

FaultKind = Literal["timeout", "error_500", "error_400", "malformed", "slow", "ack_lost"]


class FaultRule(BaseModel):
    fault: FaultKind
    path_prefix: str = "/"
    method: str | None = None
    times: int = 1  # -1 = unlimited
    delay_s: float = 0.0


class FaultRegistry:
    def __init__(self) -> None:
        self.rules: list[FaultRule] = []

    def clear(self) -> None:
        self.rules.clear()

    def take(self, method: str, path: str) -> FaultRule | None:
        for rule in self.rules:
            if not path.startswith(rule.path_prefix):
                continue
            if rule.method and rule.method.upper() != method.upper():
                continue
            if rule.times == 0:
                continue
            if rule.times > 0:
                rule.times -= 1
            return rule
        return None


async def apply_fault(
    rule: FaultRule,
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    match rule.fault:
        case "timeout":
            await asyncio.sleep(rule.delay_s or 30.0)
            return JSONResponse({"error": "gateway timeout"}, status_code=504)
        case "error_500":
            return JSONResponse({"error": "internal error (injected)"}, status_code=500)
        case "error_400":
            return JSONResponse({"error": "bad request (injected)"}, status_code=400)
        case "malformed":
            return Response(content=b'{"partial": tru', media_type="application/json")
        case "slow":
            await asyncio.sleep(rule.delay_s or 1.0)
            return await call_next(request)
        case "ack_lost":
            await call_next(request)  # side effect happens...
            return JSONResponse({"error": "service unavailable"}, status_code=503)
