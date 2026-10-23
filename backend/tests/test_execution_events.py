"""Status changes and their audit records must commit or roll back together."""
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.api import executions
from app.db.session import AsyncSessionLocal
from app.models import Execution, ExecutionEvent, ExecutionStatus
from app.services.execution_state import change_status


@pytest.fixture(autouse=True)
def cancel_transport(monkeypatch):
    import app.realtime.execution_queue as queue

    monkeypatch.setattr(queue, "remove_from_queue", AsyncMock())
    monkeypatch.setattr(queue, "signal_cancel", AsyncMock())
    monkeypatch.setattr(queue, "publish_project_event", AsyncMock())


async def test_queue_and_cancel_append_ordered_api_events(client, project, db, no_op_enqueue):
    headers = {"Authorization": f"Bearer {project['owner']['token']}"}
    response = await client.post(f"/projects/{project['id']}/executions",
                                 json={"file_id": project["file_id"]}, headers=headers)
    assert response.status_code == 202
    execution_id = response.json()["id"]
    for _ in range(2):
        response = await client.post(f"/projects/{project['id']}/executions/{execution_id}/cancel", headers=headers)
        assert response.status_code == 200
    events = (await db.execute(select(ExecutionEvent).where(
        ExecutionEvent.execution_id == execution_id).order_by(ExecutionEvent.id))).scalars().all()
    assert [(e.event_type, e.payload) for e in events] == [
        ("queued", {"from": None, "to": "queued", "by": "api"}),
        ("cancelled", {"finished_at": events[1].payload["finished_at"],
                       "from": "queued", "to": "cancelled", "by": "api"}),
    ]


async def test_status_and_event_roll_back_together(db, project):
    run = Execution(project_id=project["id"], file_id=project["file_id"], status=ExecutionStatus.QUEUED)
    db.add(run)
    await db.commit()
    execution_id = run.id
    assert await change_status(db, run, ExecutionStatus.STARTING, by="worker")
    await db.flush()
    await db.rollback()
    run = await db.get(Execution, execution_id)
    assert run.status == ExecutionStatus.QUEUED
    assert (await db.execute(select(ExecutionEvent))).scalars().all() == []


async def test_stale_worker_cannot_resurrect_cancelled_run(db, project):
    run = Execution(project_id=project["id"], file_id=project["file_id"], status=ExecutionStatus.STARTING)
    db.add(run)
    await db.commit()
    async with AsyncSessionLocal() as other:
        stale = await other.get(Execution, run.id)
        assert await change_status(db, run, ExecutionStatus.CANCELLED, by="api")
        await db.commit()
        assert not await change_status(other, stale, ExecutionStatus.RUNNING, by="worker")
        await other.commit()
    await db.refresh(run)
    assert run.status == ExecutionStatus.CANCELLED
    events = (await db.execute(select(ExecutionEvent))).scalars().all()
    assert [e.event_type for e in events] == ["cancelled"]


async def test_worker_lifecycle_records_one_event_for_every_accepted_change(db, project):
    run = Execution(project_id=project["id"], file_id=project["file_id"], status=ExecutionStatus.QUEUED)
    db.add(run)
    await db.commit()
    for status in (ExecutionStatus.STARTING, ExecutionStatus.COMPILING, ExecutionStatus.RUNNING, ExecutionStatus.COMPLETED):
        assert await change_status(db, run, status, by="worker")
    await db.commit()
    events = (await db.execute(select(ExecutionEvent).order_by(ExecutionEvent.id))).scalars().all()
    assert [e.payload for e in events] == [
        {"from": "queued", "to": "starting", "by": "worker"},
        {"from": "starting", "to": "compiling", "by": "worker"},
        {"from": "compiling", "to": "running", "by": "worker"},
        {"from": "running", "to": "completed", "by": "worker"},
    ]


async def _cancel_during_worker_updates(client, project, db, monkeypatch, initial, advances):
    """Commit real worker writes between the API's read and conditional update."""
    run = Execution(project_id=project["id"], file_id=project["file_id"], status=initial)
    db.add(run)
    await db.commit()
    original = executions.change_status
    observed = []

    async def racing_change(session, execution, target, **fields):
        observed.append(ExecutionStatus(execution.status))
        index = len(observed) - 1
        if index < len(advances):
            async with AsyncSessionLocal() as worker_db:
                current = await worker_db.get(Execution, execution.id)
                assert await original(worker_db, current, advances[index], by="worker")
                await worker_db.commit()
        return await original(session, execution, target, **fields)

    monkeypatch.setattr(executions, "change_status", racing_change)
    response = await client.post(
        f"/projects/{project['id']}/executions/{run.id}/cancel",
        headers={"Authorization": f"Bearer {project['owner']['token']}"},
    )
    assert response.status_code == 200
    await db.refresh(run)
    events = (await db.execute(select(ExecutionEvent).where(
        ExecutionEvent.execution_id == run.id).order_by(ExecutionEvent.id))).scalars().all()
    return response, run, observed, events


def _assert_cancel_notified_once(execution_id):
    import app.realtime.execution_queue as queue

    queue.remove_from_queue.assert_awaited_once_with(execution_id)
    queue.signal_cancel.assert_awaited_once_with(execution_id)
    queue.publish_project_event.assert_awaited_once()
    assert queue.publish_project_event.await_args.args[1] == {
        "type": "execution_status", "execution_id": execution_id, "status": "cancelled",
    }


@pytest.mark.parametrize("initial,advanced", [
    (ExecutionStatus.QUEUED, ExecutionStatus.STARTING),
    (ExecutionStatus.STARTING, ExecutionStatus.COMPILING),
    (ExecutionStatus.STARTING, ExecutionStatus.RUNNING),
    (ExecutionStatus.COMPILING, ExecutionStatus.RUNNING),
])
async def test_cancel_retries_when_worker_advances_to_another_active_state(
    client, project, db, monkeypatch, initial, advanced,
):
    response, run, observed, events = await _cancel_during_worker_updates(
        client, project, db, monkeypatch, initial, [advanced],
    )
    assert response.json()["status"] == "cancelled"
    assert run.status == ExecutionStatus.CANCELLED
    assert observed == [initial, advanced]
    assert [(event.event_type, event.payload["from"], event.payload["by"]) for event in events] == [
        (advanced.value, initial.value, "worker"),
        ("cancelled", advanced.value, "api"),
    ]
    _assert_cancel_notified_once(run.id)


async def test_cancel_retries_after_more_than_one_worker_advance(client, project, db, monkeypatch):
    response, run, observed, events = await _cancel_during_worker_updates(
        client, project, db, monkeypatch, ExecutionStatus.QUEUED,
        [ExecutionStatus.STARTING, ExecutionStatus.RUNNING],
    )
    assert response.json()["status"] == "cancelled"
    assert run.status == ExecutionStatus.CANCELLED
    assert observed == [ExecutionStatus.QUEUED, ExecutionStatus.STARTING, ExecutionStatus.RUNNING]
    assert [event.event_type for event in events] == ["starting", "running", "cancelled"]
    assert events[-1].payload["from"] == "running"
    assert events[-1].payload["by"] == "api"
    _assert_cancel_notified_once(run.id)


@pytest.mark.parametrize("terminal", [
    ExecutionStatus.COMPLETED, ExecutionStatus.FAILED, ExecutionStatus.TIMEOUT, ExecutionStatus.CANCELLED,
])
async def test_cancel_preserves_a_terminal_state_won_by_another_writer(
    client, project, db, monkeypatch, terminal,
):
    import app.realtime.execution_queue as queue

    response, run, observed, events = await _cancel_during_worker_updates(
        client, project, db, monkeypatch, ExecutionStatus.RUNNING, [terminal],
    )
    assert response.json()["status"] == terminal.value
    assert run.status == terminal
    assert observed == [ExecutionStatus.RUNNING]
    assert len(events) == 1
    assert events[0].event_type == terminal.value
    assert events[0].payload["by"] == "worker"
    queue.remove_from_queue.assert_not_awaited()
    queue.signal_cancel.assert_not_awaited()
    queue.publish_project_event.assert_not_awaited()
