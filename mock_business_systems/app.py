"""Simulated business systems: CRM, ticketing, knowledge base, notifications.

Runs as a separate service so network failures are realistic. Run with:
  uv run uvicorn mock_business_systems.app:app --port 8100
"""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, Header, HTTPException, Request, Response
from pydantic import BaseModel, Field

from mock_business_systems.faults import FaultRegistry, FaultRule, apply_fault
from mock_business_systems.store import WorldStore

ROOT = Path(__file__).resolve().parents[1]


class OwnerUpdate(BaseModel):
    owner_id: str


class PortalAccessGrant(BaseModel):
    access_level: Literal["standard", "admin"]


class TicketCreate(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    type: Literal["onboarding", "compliance", "escalation", "security_review", "support"]
    partner_id: str | None = None
    priority: Literal["low", "normal", "high", "urgent"] = "normal"
    owner_id: str
    description: str = ""


class NotificationCreate(BaseModel):
    channel: Literal["internal", "external"]
    to: str
    subject: str
    body: str
    partner_id: str | None = None


class ControlConfig(BaseModel):
    honor_idempotency: bool = True


def create_app(world_path: Path | None = None, policy_dir: Path | None = None) -> FastAPI:
    world_path = world_path or Path(
        os.environ.get("MOCK_WORLD_PATH", ROOT / "data" / "seed" / "world.json")
    )
    policy_dir = policy_dir or Path(os.environ.get("MOCK_POLICY_DIR", ROOT / "data" / "policies"))
    store = WorldStore(world_path, policy_dir)
    faults = FaultRegistry()
    app = FastAPI(title="WorkflowOps mock business systems", version="0.1.0")
    app.state.store = store
    app.state.faults = faults

    @app.middleware("http")
    async def fault_middleware(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if request.url.path.startswith("/_control"):
            return await call_next(request)
        header = request.headers.get("x-fault")
        rule = (
            FaultRule.model_validate({"fault": header})
            if header
            else faults.take(request.method, request.url.path)
        )
        if rule is None:
            return await call_next(request)
        return await apply_fault(rule, request, call_next)

    # ------------------------------------------------------------------ meta
    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/meta")
    async def meta() -> dict[str, Any]:
        return store.meta

    # ------------------------------------------------------------------ CRM
    @app.get("/crm/partners")
    async def list_partners(
        q: str | None = None, domain: str | None = None
    ) -> list[dict[str, Any]]:
        out = list(store.partners.values())
        if q:
            ql = q.lower()
            out = [p for p in out if ql in p["name"].lower() or ql in p["legal_name"].lower()]
        if domain:
            out = [p for p in out if p["domain"] == domain]
        return out

    @app.get("/crm/partners/{partner_id}")
    async def get_partner(partner_id: str) -> dict[str, Any]:
        if partner_id not in store.partners:
            raise HTTPException(404, "partner not found")
        return store.partners[partner_id]

    @app.put("/crm/partners/{partner_id}/owner")
    async def set_owner(
        partner_id: str, body: OwnerUpdate, idempotency_key: str | None = Header(None)
    ) -> dict[str, Any]:
        p = store.partners.get(partner_id)
        if p is None:
            raise HTTPException(404, "partner not found")
        user = store.users.get(body.owner_id)
        if user is None or not user["active"]:
            raise HTTPException(422, "owner must be an active user")
        if p["owner_id"] != body.owner_id:
            store.write_count += 1
        p["owner_id"] = body.owner_id
        p["audit"].append(
            {"op": "set_owner", "owner_id": body.owner_id, "idempotency_key": idempotency_key}
        )
        return p

    @app.put("/crm/partners/{partner_id}/portal-access")
    async def grant_access(
        partner_id: str, body: PortalAccessGrant, idempotency_key: str | None = Header(None)
    ) -> dict[str, Any]:
        p = store.partners.get(partner_id)
        if p is None:
            raise HTTPException(404, "partner not found")
        if p.get("portal_access") is None:
            store.write_count += 1
        p["portal_access"] = {
            "level": body.access_level,
            "idempotency_key": idempotency_key,
            "granted_on": store.meta["world_date"],
        }
        p["account_status"] = (
            "onboarding" if p["account_status"] == "prospect" else p["account_status"]
        )
        p["audit"].append(
            {"op": "grant_access", "level": body.access_level, "idempotency_key": idempotency_key}
        )
        return p

    @app.get("/crm/users")
    async def list_users() -> list[dict[str, Any]]:
        return list(store.users.values())

    @app.get("/crm/users/{user_id}")
    async def get_user(user_id: str) -> dict[str, Any]:
        if user_id not in store.users:
            raise HTTPException(404, "user not found")
        return store.users[user_id]

    # ------------------------------------------------------------------ ticketing
    @app.get("/ticketing/tickets")
    async def list_tickets(
        partner_id: str | None = None,
        type: str | None = None,
        status: str | None = None,
        idempotency_key: str | None = None,
    ) -> list[dict[str, Any]]:
        out = list(store.tickets.values())
        if partner_id:
            out = [t for t in out if t["partner_id"] == partner_id]
        if type:
            out = [t for t in out if t["type"] == type]
        if status:
            out = [t for t in out if t["status"] == status]
        if idempotency_key:
            out = [t for t in out if t.get("idempotency_key") == idempotency_key]
        return out

    @app.get("/ticketing/tickets/{ticket_id}")
    async def get_ticket(ticket_id: str) -> dict[str, Any]:
        if ticket_id not in store.tickets:
            raise HTTPException(404, "ticket not found")
        return store.tickets[ticket_id]

    @app.post("/ticketing/tickets", status_code=201)
    async def create_ticket(
        body: TicketCreate, response: Response, idempotency_key: str | None = Header(None)
    ) -> dict[str, Any]:
        if idempotency_key and store.honor_idempotency:
            for t in store.tickets.values():
                if t.get("idempotency_key") == idempotency_key:
                    response.status_code = 200
                    return {**t, "replayed": True}
        if body.owner_id not in store.users or not store.users[body.owner_id]["active"]:
            raise HTTPException(422, "owner must be an active user")
        tid = store.next_ticket_id()
        ticket = {
            **body.model_dump(),
            "id": tid,
            "status": "open",
            "created_at": store.meta["world_date"],
            "resolved_at": None,
            "idempotency_key": idempotency_key,
        }
        store.tickets[tid] = ticket
        store.write_count += 1
        return ticket

    # ------------------------------------------------------------------ knowledge base
    @app.get("/kb/search")
    async def kb_search(q: str, k: int = 5) -> list[dict[str, Any]]:
        hits = store.index.search(q, k)
        return [
            {
                "id": pid,
                "score": score,
                "title": store.policies[pid]["title"],
                "status": store.policies[pid]["status"],
            }
            for pid, score in hits
        ]

    @app.get("/kb/policies")
    async def list_policies() -> list[dict[str, Any]]:
        return [{k: v for k, v in p.items() if k != "body"} for p in store.policies.values()]

    @app.get("/kb/policies/{policy_id}")
    async def get_policy(policy_id: str) -> dict[str, Any]:
        if policy_id not in store.policies:
            raise HTTPException(404, "policy not found")
        return store.policies[policy_id]

    # ------------------------------------------------------------------ notifications
    @app.post("/notifications", status_code=201)
    async def send_notification(
        body: NotificationCreate, response: Response, idempotency_key: str | None = Header(None)
    ) -> dict[str, Any]:
        if idempotency_key and store.honor_idempotency:
            for n in store.notifications:
                if n.get("idempotency_key") == idempotency_key:
                    response.status_code = 200
                    return {**n, "replayed": True}
        n = {
            **body.model_dump(),
            "id": store.next_notification_id(),
            "idempotency_key": idempotency_key,
            "sent_on": store.meta["world_date"],
        }
        store.notifications.append(n)
        store.write_count += 1
        return n

    @app.get("/notifications")
    async def list_notifications(
        partner_id: str | None = None, idempotency_key: str | None = None
    ) -> list[dict[str, Any]]:
        out = store.notifications
        if partner_id:
            out = [n for n in out if n.get("partner_id") == partner_id]
        if idempotency_key:
            out = [n for n in out if n.get("idempotency_key") == idempotency_key]
        return out

    # ------------------------------------------------------------------ control plane
    @app.post("/_control/reset")
    async def reset() -> dict[str, str]:
        store.reset()
        faults.clear()
        return {"status": "reset"}

    @app.post("/_control/faults")
    async def set_faults(rules: list[FaultRule]) -> dict[str, int]:
        faults.rules = rules
        return {"rules": len(rules)}

    @app.delete("/_control/faults")
    async def clear_faults() -> dict[str, int]:
        faults.clear()
        return {"rules": 0}

    @app.post("/_control/config")
    async def set_config(cfg: ControlConfig) -> dict[str, bool]:
        store.honor_idempotency = cfg.honor_idempotency
        return {"honor_idempotency": store.honor_idempotency}

    @app.get("/_control/state")
    async def state() -> dict[str, Any]:
        return store.snapshot()

    return app


app = create_app()
