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


async def test_illegal_status_change_leaves_row_and_metadata_unchanged(db, project, caplog):
    from sqlalchemy import select

    from app.models import Execution, ExecutionEvent
    from app.services.execution_state import change_status

    run = Execution(project_id=project["id"], file_id=project["file_id"], status=ExecutionStatus.CANCELLED)
    db.add(run)
    await db.commit()
    assert not await change_status(db, run, ExecutionStatus.RUNNING, by="worker", stdout="late")
    await db.commit()
    await db.refresh(run)
    assert run.status == ExecutionStatus.CANCELLED
    assert run.stdout == ""
    assert (await db.execute(select(ExecutionEvent))).scalars().all() == []
    assert "Refused" in caplog.text


async def test_cancellation_is_allowed_while_starting(db, project):
    from app.models import Execution
    from app.services.execution_state import change_status

    run = Execution(project_id=project["id"], file_id=project["file_id"], status=ExecutionStatus.STARTING)
    db.add(run)
    await db.commit()
    assert await change_status(db, run, ExecutionStatus.CANCELLED, by="api")
    await db.commit()
    await db.refresh(run)
    assert run.status == ExecutionStatus.CANCELLED
