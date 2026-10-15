"""Test the execution status state machine."""

from app.models.execution import (
    ALLOWED_TRANSITIONS,
    ExecutionStatus,
    can_transition,
)


def test_initial_transitions():
    assert can_transition(ExecutionStatus.QUEUED, ExecutionStatus.STARTING)
    assert can_transition(ExecutionStatus.QUEUED, ExecutionStatus.CANCELLED)
    assert can_transition(ExecutionStatus.QUEUED, ExecutionStatus.FAILED)  # queue delivery failed
    assert not can_transition(ExecutionStatus.QUEUED, ExecutionStatus.RUNNING)
    assert not can_transition(ExecutionStatus.QUEUED, ExecutionStatus.COMPLETED)


def test_running_terminal_paths():
    for terminal in {
        ExecutionStatus.COMPLETED,
        ExecutionStatus.FAILED,
        ExecutionStatus.TIMEOUT,
        ExecutionStatus.CANCELLED,
    }:
        assert can_transition(ExecutionStatus.RUNNING, terminal)


def test_no_transitions_from_terminal_states():
    for terminal in {
        ExecutionStatus.COMPLETED,
        ExecutionStatus.FAILED,
        ExecutionStatus.TIMEOUT,
        ExecutionStatus.CANCELLED,
    }:
        assert ALLOWED_TRANSITIONS[terminal] == set()


def test_every_state_has_a_transition_table_entry():
    for s in ExecutionStatus:
        assert s in ALLOWED_TRANSITIONS
