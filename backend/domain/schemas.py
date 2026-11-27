"""Typed contracts shared by the API, engine, adapters, and evaluation."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.domain.enums import (
    ActionStatus,
    ApprovalDecisionType,
    ApprovalStatus,
    Priority,
    RequestStatus,
    RiskClass,
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------- requests
class Attachment(StrictModel):
    name: str = Field(max_length=200)
    content: str = Field(default="", max_length=20_000)


class RequestCreate(StrictModel):
    description: str = Field(min_length=1, max_length=10_000)
    # The requester is the signed-in user; department defaults to theirs.
    department: str | None = Field(default=None, max_length=64)
    category: str | None = Field(default=None, max_length=64)
    priority: Priority = Priority.NORMAL
    attachments: list[Attachment] = Field(default_factory=list, max_length=10)


class RequestRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    description: str
    requester_id: str
    department: str
    category: str | None
    priority: Priority
    attachments: list[Attachment]
    status: RequestStatus
    status_reason: str | None
    created_at: datetime
    updated_at: datetime


# --------------------------------------------------------------------------- stage outputs
RequestType = Literal["partner_onboarding", "access_request", "unknown"]


class Classification(StrictModel):
    request_type: RequestType
    in_scope: bool
    partner_name: str | None = None
    partner_id: str | None = None
    missing_info: list[str] = Field(default_factory=list)
    urgency: Priority = Priority.NORMAL
    needs_human_escalation: bool = False
    rationale: str = ""


class Evidence(StrictModel):
    ref: str  # e.g. "policy:POL-ONB-002", "crm:partner-0012", "ticket:TCK-00042"
    source: Literal["crm", "ticketing", "knowledge_base", "request", "users"]
    summary: str
    untrusted: bool = True  # retrieved/user text is always data, never instructions


class Finding(StrictModel):
    code: str  # machine-readable, e.g. "missing_soc2", "duplicate_partner"
    detail: str
    evidence_refs: list[str] = Field(default_factory=list)
    severity: Literal["info", "warning", "blocking"] = "info"
    explicit_policy: bool = False  # True when backed by explicit policy text (vs inferred)


class InvestigationReport(StrictModel):
    agent: Literal["research", "policy", "data"]
    findings: list[Finding] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    facts: dict[str, Any] = Field(default_factory=dict)


class ProposedAction(StrictModel):
    key: str  # stable id within a plan, e.g. "a1"
    action_type: str
    params: dict[str, Any]
    evidence_refs: list[str] = Field(default_factory=list)
    risk_class: RiskClass  # proposed by planner; the policy engine recomputes it
    expected_outcome: str = ""
    depends_on: list[str] = Field(default_factory=list)
    owner: str = "platform"
    idempotency_key: str = ""


class ActionPlan(StrictModel):
    actions: list[ProposedAction]
    rationale: str = ""
    requires_requester_info: list[str] = Field(default_factory=list)


class ValidationIssue(StrictModel):
    action_key: str | None
    check: str  # schema | required_fields | allowlist | permission | business_rule | duplicate
    message: str
    blocking: bool = True


class ValidationReport(StrictModel):
    issues: list[ValidationIssue] = Field(default_factory=list)
    accepted: list[str] = Field(default_factory=list)
    rejected: list[str] = Field(default_factory=list)
    effective_risk: dict[str, RiskClass] = Field(default_factory=dict)
    needs_approval: list[str] = Field(default_factory=list)


class Report(StrictModel):
    summary: str
    outcome: RequestStatus
    actions_done: list[str] = Field(default_factory=list)
    actions_not_done: list[str] = Field(default_factory=list)
    next_steps: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- read models
class ProposedActionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    run_id: uuid.UUID
    key: str
    action_type: str
    params: dict[str, Any]
    evidence_refs: list[str]
    risk_class: RiskClass
    status: ActionStatus
    idempotency_key: str
    payload_hash: str
    validation_notes: list[str]
    result: dict[str, Any] | None


class ApprovalRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    action_id: uuid.UUID
    run_id: uuid.UUID
    status: ApprovalStatus
    action_hash: str
    risk_class: RiskClass
    expires_at: datetime
    decided_by: str | None
    decided_at: datetime | None
    comment: str | None
    created_at: datetime


class ApprovalDecisionIn(StrictModel):
    decision: ApprovalDecisionType
    action_hash: str = Field(min_length=64, max_length=64, description="hash the reviewer saw")
    comment: str | None = Field(default=None, max_length=2000)


class WorkflowRunCreate(StrictModel):
    framework: str = "rules"
    mode: Literal["stage_executor", "native"] = "stage_executor"
    model_config_override: dict[str, Any] | None = Field(default=None, alias="model_config")

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class WorkflowRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    request_id: uuid.UUID
    framework: str
    mode: str
    status: str
    current_stage: str | None
    state: dict[str, Any]
    error: str | None
    created_at: datetime
    updated_at: datetime


class EventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    run_id: uuid.UUID
    seq: int
    event_type: str
    stage: str | None
    agent: str | None
    payload: dict[str, Any]
    created_at: datetime
