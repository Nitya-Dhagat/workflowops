"""The single source of truth for legal request-status transitions.

Every status change in the platform goes through :func:`assert_transition`.
"""

from __future__ import annotations

from backend.domain.enums import TERMINAL_STATUSES, RequestStatus

S = RequestStatus

_FAILABLE = {S.FAILED, S.CANCELLED}

LEGAL_TRANSITIONS: dict[RequestStatus, frozenset[RequestStatus]] = {
    S.RECEIVED: frozenset({S.CLASSIFYING} | _FAILABLE),
    # Out-of-scope / invalid requests are rejected right after classification.
    S.CLASSIFYING: frozenset({S.INVESTIGATING, S.AWAITING_REQUESTER_INFO} | _FAILABLE),
    S.INVESTIGATING: frozenset({S.PLANNING} | _FAILABLE),
    S.PLANNING: frozenset({S.VALIDATING} | _FAILABLE),
    # Validation may send the plan back for refinement (bounded in the engine).
    S.VALIDATING: frozenset(
        {
            S.PLANNING,
            S.AWAITING_APPROVAL,
            S.EXECUTING,
            S.AWAITING_REQUESTER_INFO,
            S.COMPLETED,
            S.COMPLETED_WITH_WARNINGS,
        }
        | _FAILABLE
    ),
    # When no approved action remains (all rejected/expired) the run finishes directly.
    S.AWAITING_APPROVAL: frozenset(
        {S.EXECUTING, S.PLANNING, S.COMPLETED, S.COMPLETED_WITH_WARNINGS, S.AWAITING_REQUESTER_INFO}
        | _FAILABLE
    ),
    S.EXECUTING: frozenset({S.VERIFYING} | _FAILABLE),
    S.VERIFYING: frozenset(
        {S.COMPLETED, S.COMPLETED_WITH_WARNINGS, S.AWAITING_REQUESTER_INFO} | _FAILABLE
    ),
    # Requester supplied missing info -> re-run from classification.
    S.AWAITING_REQUESTER_INFO: frozenset({S.CLASSIFYING} | _FAILABLE),
    S.COMPLETED: frozenset(),
    S.COMPLETED_WITH_WARNINGS: frozenset(),
    S.FAILED: frozenset(),
    S.CANCELLED: frozenset(),
}


class IllegalTransitionError(ValueError):
    def __init__(self, current: RequestStatus, target: RequestStatus) -> None:
        super().__init__(f"illegal transition {current.value} -> {target.value}")
        self.current = current
        self.target = target


def can_transition(current: RequestStatus, target: RequestStatus) -> bool:
    return target in LEGAL_TRANSITIONS[current]


def assert_transition(current: RequestStatus, target: RequestStatus) -> None:
    if not can_transition(current, target):
        raise IllegalTransitionError(current, target)


def is_terminal(status: RequestStatus) -> bool:
    return status in TERMINAL_STATUSES
