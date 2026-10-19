"""Status changes and their audit records must commit or roll back together."""
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

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
