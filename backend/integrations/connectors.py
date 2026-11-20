"""Connectors for the simulated CRM, ticketing, knowledge base and notification systems."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from backend.integrations.base import (
    ConnectorAction,
    ConnectorError,
    ConnectorResult,
    HealthStatus,
    HttpTransport,
    RetrieveQuery,
)

ReadOp = Callable[[dict[str, Any]], Awaitable[ConnectorResult]]
WriteOp = Callable[[dict[str, Any], str], Awaitable[ConnectorResult]]


class _HttpConnector:
    name: str = "base"

    def __init__(self, transport: HttpTransport) -> None:
        self.t = transport
        self._reads: dict[str, ReadOp] = {}
        self._writes: dict[str, WriteOp] = {}

    async def health_check(self) -> HealthStatus:
        start = time.perf_counter()
        try:
            await self.t.call("GET", "/health")
            ok, detail = True, "ok"
        except ConnectorError as e:
            ok, detail = False, str(e)
        return HealthStatus(
            name=self.name, ok=ok, latency_ms=(time.perf_counter() - start) * 1000, detail=detail
        )

    async def retrieve(self, query: RetrieveQuery) -> ConnectorResult:
        op = self._reads.get(query.op)
        if op is None:
            raise ConnectorError(f"{self.name}: unknown read op {query.op}", retryable=False)
        return await op(query.params)

    async def execute(self, action: ConnectorAction) -> ConnectorResult:
        op = self._writes.get(action.op)
        if op is None:
            raise ConnectorError(f"{self.name}: unknown write op {action.op}", retryable=False)
        return await op(action.params, action.idempotency_key)


class CRMConnector(_HttpConnector):
    name = "crm"

    def __init__(self, transport: HttpTransport) -> None:
        super().__init__(transport)
        t = self.t
        self._reads = {
            "meta": lambda p: t.call("GET", "/meta"),
            "search_partners": lambda p: t.call("GET", "/crm/partners", params=p),
            "get_partner": lambda p: t.call("GET", f"/crm/partners/{p['partner_id']}"),
            "get_user": lambda p: t.call("GET", f"/crm/users/{p['user_id']}"),
            "list_users": lambda p: t.call("GET", "/crm/users"),
        }
        self._writes = {
            "set_owner": lambda p, k: t.call(
                "PUT",
                f"/crm/partners/{p['partner_id']}/owner",
                json_body={"owner_id": p["owner_id"]},
                idempotency_key=k,
            ),
            "grant_portal_access": lambda p, k: t.call(
                "PUT",
                f"/crm/partners/{p['partner_id']}/portal-access",
                json_body={"access_level": p["access_level"]},
                idempotency_key=k,
            ),
        }


class TicketingConnector(_HttpConnector):
    name = "ticketing"

    def __init__(self, transport: HttpTransport) -> None:
        super().__init__(transport)
        t = self.t
        self._reads = {
            "search_tickets": lambda p: t.call("GET", "/ticketing/tickets", params=p),
            "get_ticket": lambda p: t.call("GET", f"/ticketing/tickets/{p['ticket_id']}"),
        }
        self._writes = {
            "create_ticket": lambda p, k: t.call(
                "POST", "/ticketing/tickets", json_body=p, idempotency_key=k
            ),
        }


class KnowledgeBaseConnector(_HttpConnector):
    name = "knowledge_base"

    def __init__(self, transport: HttpTransport) -> None:
        super().__init__(transport)
        t = self.t
        self._reads = {
            "search": lambda p: t.call("GET", "/kb/search", params=p),
            "get_policy": lambda p: t.call("GET", f"/kb/policies/{p['policy_id']}"),
            "list_policies": lambda p: t.call("GET", "/kb/policies"),
        }


class NotificationConnector(_HttpConnector):
    name = "notifications"

    def __init__(self, transport: HttpTransport) -> None:
        super().__init__(transport)
        t = self.t
        self._reads = {
            "search_notifications": lambda p: t.call("GET", "/notifications", params=p),
        }
        self._writes = {
            "send": lambda p, k: t.call("POST", "/notifications", json_body=p, idempotency_key=k),
        }


class ConnectorSet:
    """All connectors sharing one HTTP client to the mock business systems."""

    def __init__(self, client: httpx.AsyncClient, timeout_s: float = 5.0) -> None:
        transport = HttpTransport(client, timeout_s)
        self.client = client
        self.crm = CRMConnector(transport)
        self.ticketing = TicketingConnector(transport)
        self.knowledge_base = KnowledgeBaseConnector(transport)
        self.notifications = NotificationConnector(transport)

    def by_name(self, name: str) -> _HttpConnector:
        mapping: dict[str, _HttpConnector] = {
            "crm": self.crm,
            "ticketing": self.ticketing,
            "knowledge_base": self.knowledge_base,
            "notifications": self.notifications,
        }
        return mapping[name]

    def all(self) -> list[_HttpConnector]:
        return [self.crm, self.ticketing, self.knowledge_base, self.notifications]
