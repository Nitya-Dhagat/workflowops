"""How each catalog action maps onto connector calls, and how to read its effect back.

``locate`` is used twice: (1) before retrying an ambiguous write, to avoid double side
effects; (2) in the verification stage, to independently confirm the effect exists.
"""

from __future__ import annotations

from typing import Any

from backend.tools.gateway import ToolGateway


def connector_params(action_type: str, params: dict[str, Any]) -> dict[str, Any]:
    p = params
    match action_type:
        case "create_onboarding_task":
            return {
                "title": p["title"],
                "type": "onboarding",
                "partner_id": p["partner_id"],
                "priority": "normal",
                "owner_id": p["owner_id"],
                "description": "Created by WorkflowOps partner_onboarding workflow.",
            }
        case "request_compliance_doc":
            return {
                "channel": "internal",
                "to": p["requester_id"],
                "partner_id": p["partner_id"],
                "subject": f"Compliance document needed: {p['doc_type']}",
                "body": f"Please obtain a current {p['doc_type']} report for partner "
                f"{p['partner_id']} (POL-ONB-002).",
            }
        case "assign_account_owner":
            return {"partner_id": p["partner_id"], "owner_id": p["owner_id"]}
        case "grant_portal_access":
            return {"partner_id": p["partner_id"], "access_level": p["access_level"]}
        case "send_external_notification":
            return {
                "channel": "external",
                "to": p["contact_id"],
                "partner_id": p["partner_id"],
                "subject": "Welcome to the partner program",
                "body": "Your partner onboarding has started.",
            }
        case "escalate_to_human":
            return {
                "title": f"Escalation: {p['finding_code']}",
                "type": "escalation",
                "partner_id": p.get("partner_id"),
                "priority": "high",
                "owner_id": p["assignee_id"],
                "description": p["reason"],
            }
        case "create_security_review_task":
            return {
                "title": f"Security review for {p['partner_id']}",
                "type": "security_review",
                "partner_id": p["partner_id"],
                "priority": "normal",
                "owner_id": p["owner_id"],
                "description": "Required by POL-SEC-002.",
            }
    raise ValueError(f"no binding for {action_type}")


def external_ref(action_type: str, data: Any) -> str | None:
    if not isinstance(data, dict):
        return None
    if action_type in {"assign_account_owner", "grant_portal_access"}:
        return f"crm:{data.get('id')}"
    if "id" in data:
        prefix = "ticket" if str(data["id"]).startswith("TCK") else "notification"
        return f"{prefix}:{data['id']}"
    return None


async def locate(
    gateway: ToolGateway, action_type: str, params: dict[str, Any], idem_key: str
) -> str | None:
    """Return an external ref if the action's effect exists in the system of record."""
    g = gateway
    if action_type in {
        "create_onboarding_task",
        "escalate_to_human",
        "create_security_review_task",
    }:
        tickets = await g.call(
            agent="platform",
            tool="ticketing.search_tickets",
            args={"partner_id": params.get("partner_id")},
        )
        hit = [t for t in tickets if t.get("idempotency_key") == idem_key]
        return f"ticket:{hit[0]['id']}" if hit else None
    if action_type in {"request_compliance_doc", "send_external_notification"}:
        notes = await g.call(
            agent="platform", tool="notifications.search", args={"idempotency_key": idem_key}
        )
        return f"notification:{notes[0]['id']}" if notes else None
    partner = await g.call(
        agent="platform", tool="crm.get_partner", args={"partner_id": params["partner_id"]}
    )
    if action_type == "assign_account_owner":
        return f"crm:{partner['id']}" if partner["owner_id"] == params["owner_id"] else None
    if action_type == "grant_portal_access":
        acc = partner.get("portal_access") or {}
        return f"crm:{partner['id']}" if acc.get("level") == params["access_level"] else None
    return None
