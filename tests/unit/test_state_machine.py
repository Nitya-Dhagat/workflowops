from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from backend.domain.enums import TERMINAL_STATUSES, RequestStatus
from backend.domain.state_machine import (
    LEGAL_TRANSITIONS,
    IllegalTransitionError,
    assert_transition,
    can_transition,
    is_terminal,
)

S = RequestStatus


def test_every_status_has_transition_entry() -> None:
    assert set(LEGAL_TRANSITIONS) == set(RequestStatus)


def test_terminal_statuses_have_no_exits() -> None:
    for s in TERMINAL_STATUSES:
        assert LEGAL_TRANSITIONS[s] == frozenset()
        assert is_terminal(s)


def test_every_status_reachable_from_received() -> None:
    seen, frontier = {S.RECEIVED}, [S.RECEIVED]
    while frontier:
        cur = frontier.pop()
        for nxt in LEGAL_TRANSITIONS[cur]:
            if nxt not in seen:
                seen.add(nxt)
                frontier.append(nxt)
    assert seen == set(RequestStatus)


def test_every_non_terminal_can_fail_or_cancel() -> None:
    for s in RequestStatus:
        if not is_terminal(s):
            assert can_transition(s, S.FAILED) and can_transition(s, S.CANCELLED)


@pytest.mark.parametrize(
    ("a", "b"),
    [
        (S.RECEIVED, S.EXECUTING),  # cannot skip validation/approval
        (S.PLANNING, S.EXECUTING),
        (S.INVESTIGATING, S.AWAITING_APPROVAL),
        (S.COMPLETED, S.RECEIVED),
        (S.AWAITING_APPROVAL, S.VERIFYING),
    ],
)
def test_illegal_transitions_raise(a: S, b: S) -> None:
    with pytest.raises(IllegalTransitionError):
        assert_transition(a, b)


def test_execution_only_reachable_through_validation_or_approval() -> None:
    sources = {s for s, targets in LEGAL_TRANSITIONS.items() if S.EXECUTING in targets}
    assert sources == {S.VALIDATING, S.AWAITING_APPROVAL}


@given(st.lists(st.sampled_from(list(RequestStatus)), max_size=30))
def test_random_walk_never_accepts_illegal_edge(path: list[RequestStatus]) -> None:
    cur = S.RECEIVED
    for nxt in path:
        if can_transition(cur, nxt):
            assert_transition(cur, nxt)
            cur = nxt
        else:
            with pytest.raises(IllegalTransitionError):
                assert_transition(cur, nxt)
