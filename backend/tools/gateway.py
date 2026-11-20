"""Tool gateway: the ONLY path from agents (or the executor) to external systems.

It enforces per-agent allowlists, validates arguments, redacts what it logs, and records
every invocation. Side-effecting calls are only reachable through :meth:`execute_action`,
which requires an approved, hash-matching action.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

from backend.core.logging import redact
from backend.domain.enums import ActionStatus
from backend.integrations.base import (
    ConnectorAction,
    ConnectorError,
    ConnectorResult,
    RetrieveQuery,
)
from backend.integrations.connectors import ConnectorSet
from backend.policy.catalog import CATALOG, action_hash


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NoArgs(_Args):
    pass


class SearchPartnersArgs(_Args):
    q: str | None = None
    domain: str | None = None


class PartnerIdArgs(_Args):
    partner_id: str


class SearchTicketsArgs(_Args):
    partner_id: str | None = None
    type: str | None = None
    status: str | None = None


class KBSearchArgs(_Args):
    q: str
    k: int = 5


class PolicyIdArgs(_Args):
    policy_id: str


class NotificationSearchArgs(_Args):
    partner_id: str | None = None
    idempotency_key: str | None = None


@dataclass(frozen=True)
class ToolSpec:
    name: str
    connector: str
    op: str
    args_model: type[_Args]


READ_TOOLS: dict[str, ToolSpec] = {
    t.name: t
    for t in [
        ToolSpec("crm.meta", "crm", "meta", NoArgs),
        ToolSpec("crm.search_partners", "crm", "search_partners", SearchPartnersArgs),
        ToolSpec("crm.get_partner", "crm", "get_partner", PartnerIdArgs),
        ToolSpec("crm.list_users", "crm", "list_users", NoArgs),
        ToolSpec("ticketing.search_tickets", "ticketing", "search_tickets", SearchTicketsArgs),
        ToolSpec("kb.search", "knowledge_base", "search", KBSearchArgs),
        ToolSpec("kb.get_policy", "knowledge_base", "get_policy", PolicyIdArgs),
        ToolSpec("kb.list_policies", "knowledge_base", "list_policies", NoArgs),
        ToolSpec(
            "notifications.search", "notifications", "search_notifications", NotificationSearchArgs
        ),
    ]
}

AGENT_ALLOWLIST: dict[str, frozenset[str]] = {
    "intake": frozenset({"crm.search_partners"}),
    "research": frozenset({"kb.search", "kb.get_policy"}),
    "policy": frozenset({"kb.search", "kb.get_policy", "kb.list_policies"}),
    "data": frozenset(
        {
            "crm.meta",
            "crm.get_partner",
            "crm.search_partners",
            "crm.list_users",
            "ticketing.search_tickets",
        }
    ),
    "planning": frozenset({"crm.list_users"}),
    "validation": frozenset(),
    "reporting": frozenset(),
    # The platform itself (fact gathering for the policy engine, verification).
    "platform": frozenset(READ_TOOLS),
}


class ToolDeniedError(Exception):
    pass


class ToolArgumentError(Exception):
    pass


class UnauthorizedExecutionError(Exception):
    pass


@dataclass
class InvocationRecord:
    run_id: uuid.UUID | None
    agent: str
    tool: str
    status: str
    args_redacted: dict[str, Any]
    result_redacted: dict[str, Any] | None
    error: str | None
    latency_ms: float


@dataclass
class ToolGateway:
    connectors: ConnectorSet
    records: list[InvocationRecord] = field(default_factory=list)

    def drain(self) -> list[InvocationRecord]:
        out, self.records = self.records, []
        return out

    def _record(
        self,
        run_id: uuid.UUID | None,
        agent: str,
        tool: str,
        status: str,
        args: dict[str, Any],
        result: Any,
        error: str | None,
        start: float,
    ) -> None:
        summary: dict[str, Any] | None = None
        if result is not None:
            summary = {"items": len(result)} if isinstance(result, list) else redact(result)
        self.records.append(
            InvocationRecord(
                run_id=run_id,
                agent=agent,
                tool=tool,
                status=status,
                args_redacted=redact(args),
                result_redacted=summary,
                error=error,
                latency_ms=(time.perf_counter() - start) * 1000,
            )
        )

    async def call(
        self, *, agent: str, tool: str, args: dict[str, Any], run_id: uuid.UUID | None = None
    ) -> Any:
        start = time.perf_counter()
        spec = READ_TOOLS.get(tool)
        if spec is None or tool not in AGENT_ALLOWLIST.get(agent, frozenset()):
            self._record(run_id, agent, tool, "denied", args, None, "not on allowlist", start)
            raise ToolDeniedError(f"agent `{agent}` may not call `{tool}`")
        try:
            parsed = spec.args_model.model_validate(args)
        except ValidationError as e:
            self._record(run_id, agent, tool, "error", args, None, "invalid arguments", start)
            raise ToolArgumentError(str(e)) from e
        params = parsed.model_dump(exclude_none=True)
        try:
            res = await self.connectors.by_name(spec.connector).retrieve(
                RetrieveQuery(op=spec.op, params=params)
            )
        except ConnectorError as e:
            self._record(run_id, agent, tool, "error", params, None, str(e), start)
            raise
        self._record(run_id, agent, tool, "ok", params, res.data, None, start)
        return res.data

    async def execute_action(
        self,
        *,
        action_type: str,
        params: dict[str, Any],
        status: ActionStatus,
        approved_hash: str,
        idempotency_key: str,
        connector_params: dict[str, Any],
        run_id: uuid.UUID | None = None,
    ) -> ConnectorResult:
        """Perform a side effect. Refuses anything not approved with a matching hash."""
        start = time.perf_counter()
        tool = f"action.{action_type}"
        spec = CATALOG.get(action_type)
        if spec is None:
            self._record(run_id, "execution", tool, "denied", params, None, "unknown", start)
            raise UnauthorizedExecutionError(f"unknown action type {action_type}")
        if status is not ActionStatus.APPROVED and status is not ActionStatus.EXECUTING:
            self._record(run_id, "execution", tool, "denied", params, None, "not approved", start)
            raise UnauthorizedExecutionError(f"action not approved (status={status})")
        if action_hash(action_type, params) != approved_hash:
            self._record(run_id, "execution", tool, "denied", params, None, "hash mismatch", start)
            raise UnauthorizedExecutionError("action changed after approval; re-approval needed")
        try:
            res = await self.connectors.by_name(spec.connector).execute(
                ConnectorAction(
                    op=spec.op, params=connector_params, idempotency_key=idempotency_key
                )
            )
        except ConnectorError as e:
            self._record(run_id, "execution", tool, "error", connector_params, None, str(e), start)
            raise
        self._record(run_id, "execution", tool, "ok", connector_params, res.data, None, start)
        return res
