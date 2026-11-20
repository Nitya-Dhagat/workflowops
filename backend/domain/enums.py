"""Explicit status and classification enums shared across the platform."""

from __future__ import annotations

from enum import StrEnum


class RequestStatus(StrEnum):
    RECEIVED = "received"
    CLASSIFYING = "classifying"
    INVESTIGATING = "investigating"
    PLANNING = "planning"
    VALIDATING = "validating"
    AWAITING_APPROVAL = "awaiting_approval"
    AWAITING_REQUESTER_INFO = "awaiting_requester_info"
    EXECUTING = "executing"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    COMPLETED_WITH_WARNINGS = "completed_with_warnings"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_STATUSES: frozenset[RequestStatus] = frozenset(
    {
        RequestStatus.COMPLETED,
        RequestStatus.COMPLETED_WITH_WARNINGS,
        RequestStatus.FAILED,
        RequestStatus.CANCELLED,
    }
)


class Priority(StrEnum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"


class RiskClass(StrEnum):
    LOW = "low"  # read-only
    MEDIUM = "medium"  # internal task create/assign, non-sensitive status
    HIGH = "high"  # access, financial, external commitments, deletes


class ActionStatus(StrEnum):
    PROPOSED = "proposed"
    REJECTED_BY_VALIDATION = "rejected_by_validation"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    CHANGES_REQUESTED = "changes_requested"
    EXPIRED = "expired"
    EXECUTING = "executing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    VERIFIED = "verified"
    VERIFICATION_FAILED = "verification_failed"
    SKIPPED = "skipped"  # a dependency was not executed


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    CHANGES_REQUESTED = "changes_requested"
    EXPIRED = "expired"


class ApprovalDecisionType(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"
    REQUEST_CHANGES = "request_changes"


class UserRole(StrEnum):
    REQUESTER = "requester"
    REVIEWER = "reviewer"


class RunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
