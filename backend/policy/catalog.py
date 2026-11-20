"""Action catalog: the allowlist of side-effecting actions, their typed params and risk.

Risk is defined here, by the platform. A planner's proposed risk class is advisory only.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.domain.enums import RiskClass


class _Params(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateOnboardingTaskParams(_Params):
    partner_id: str
    owner_id: str
    title: str = Field(min_length=3, max_length=200)


class RequestComplianceDocParams(_Params):
    partner_id: str
    doc_type: Literal["SOC2_TYPE_II"]
    requester_id: str


class AssignAccountOwnerParams(_Params):
    partner_id: str
    owner_id: str


class GrantPortalAccessParams(_Params):
    partner_id: str
    access_level: Literal["standard", "admin"]


class SendExternalNotificationParams(_Params):
    partner_id: str
    contact_id: str
    template: Literal["welcome"]


class EscalateToHumanParams(_Params):
    partner_id: str | None = None
    finding_code: str
    reason: str = Field(min_length=3, max_length=1000)
    assignee_id: str


class CreateSecurityReviewTaskParams(_Params):
    partner_id: str
    owner_id: str


@dataclass(frozen=True)
class ActionSpec:
    name: str
    risk: RiskClass
    params_model: type[_Params]
    connector: str
    op: str
    description: str


CATALOG: dict[str, ActionSpec] = {
    s.name: s
    for s in [
        ActionSpec(
            "create_onboarding_task",
            RiskClass.MEDIUM,
            CreateOnboardingTaskParams,
            "ticketing",
            "create_ticket",
            "Create the internal onboarding task",
        ),
        ActionSpec(
            "request_compliance_doc",
            RiskClass.MEDIUM,
            RequestComplianceDocParams,
            "notifications",
            "send",
            "Ask the requester to obtain a compliance doc",
        ),
        ActionSpec(
            "assign_account_owner",
            RiskClass.MEDIUM,
            AssignAccountOwnerParams,
            "crm",
            "set_owner",
            "Reassign the partner's account owner",
        ),
        ActionSpec(
            "grant_portal_access",
            RiskClass.HIGH,
            GrantPortalAccessParams,
            "crm",
            "grant_portal_access",
            "Grant partner portal access",
        ),
        ActionSpec(
            "send_external_notification",
            RiskClass.HIGH,
            SendExternalNotificationParams,
            "notifications",
            "send",
            "Send an external notification to the partner",
        ),
        ActionSpec(
            "escalate_to_human",
            RiskClass.MEDIUM,
            EscalateToHumanParams,
            "ticketing",
            "create_ticket",
            "Open a high-priority escalation ticket",
        ),
        ActionSpec(
            "create_security_review_task",
            RiskClass.MEDIUM,
            CreateSecurityReviewTaskParams,
            "ticketing",
            "create_ticket",
            "Create a security review task",
        ),
    ]
}


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def action_hash(action_type: str, params: dict[str, Any]) -> str:
    """Hash of exactly what a reviewer approves. Any change to params changes the hash."""
    return hashlib.sha256(canonical_json({"t": action_type, "p": params}).encode()).hexdigest()


def idempotency_key(request_id: str, action_type: str, params: dict[str, Any]) -> str:
    digest = hashlib.sha256(
        canonical_json({"r": request_id, "t": action_type, "p": params}).encode()
    ).hexdigest()
    return f"wfo-{digest[:40]}"
