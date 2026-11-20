"""Common connector interface and error classification."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Literal, Protocol

import httpx
from pydantic import BaseModel, Field


class ConnectorError(Exception):
    """A connector call failed.

    ``retryable`` distinguishes transient failures (timeouts, 5xx, malformed bodies) from
    permanent ones (4xx). ``ambiguous`` marks failures where a write may have been applied
    (timeouts, 5xx, malformed responses), so callers must read back before retrying.
    """

    def __init__(
        self, message: str, *, retryable: bool, ambiguous: bool = False, status: int | None = None
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.ambiguous = ambiguous
        self.status = status


class HealthStatus(BaseModel):
    name: str
    ok: bool
    latency_ms: float
    detail: str = ""


class RetrieveQuery(BaseModel):
    op: str
    params: dict[str, Any] = Field(default_factory=dict)


class ConnectorAction(BaseModel):
    op: str
    params: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str


class ConnectorResult(BaseModel):
    ok: bool
    data: Any = None
    status: int = 200
    latency_ms: float = 0.0
    replayed: bool = False


class Connector(Protocol):
    name: str

    async def health_check(self) -> HealthStatus: ...
    async def retrieve(self, query: RetrieveQuery) -> ConnectorResult: ...
    async def execute(self, action: ConnectorAction) -> ConnectorResult: ...


class HttpTransport:
    """Thin HTTP wrapper: enforces a timeout and classifies errors."""

    def __init__(self, client: httpx.AsyncClient, timeout_s: float) -> None:
        self.client = client
        self.timeout_s = timeout_s

    async def call(
        self,
        method: Literal["GET", "POST", "PUT"],
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> ConnectorResult:
        headers = {"Idempotency-Key": idempotency_key} if idempotency_key else {}
        write = method != "GET"
        start = time.perf_counter()
        try:
            async with asyncio.timeout(self.timeout_s):
                resp = await self.client.request(
                    method, path, params=params, json=json_body, headers=headers
                )
        except TimeoutError as e:
            raise ConnectorError(f"timeout calling {path}", retryable=True, ambiguous=write) from e
        except httpx.TransportError as e:
            raise ConnectorError(f"transport error: {e}", retryable=True, ambiguous=write) from e
        latency = (time.perf_counter() - start) * 1000
        if resp.status_code >= 500 or resp.status_code == 429:
            raise ConnectorError(
                f"{resp.status_code} from {path}",
                retryable=True,
                ambiguous=write,
                status=resp.status_code,
            )
        if resp.status_code >= 400:
            raise ConnectorError(
                f"{resp.status_code} from {path}: {resp.text[:200]}",
                retryable=False,
                status=resp.status_code,
            )
        try:
            data = resp.json()
        except (json.JSONDecodeError, ValueError) as e:
            raise ConnectorError(
                f"malformed JSON from {path}",
                retryable=True,
                ambiguous=write,
                status=resp.status_code,
            ) from e
        replayed = isinstance(data, dict) and bool(data.get("replayed"))
        return ConnectorResult(
            ok=True, data=data, status=resp.status_code, latency_ms=latency, replayed=replayed
        )
