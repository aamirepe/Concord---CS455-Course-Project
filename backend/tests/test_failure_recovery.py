"""Recover durable state after queue, worker and backend failures."""
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, Mock

from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy import select

from app.api import executions
from app.models import Execution, ExecutionEvent, ExecutionStatus
from app.realtime import orphan_sweep, rooms
from app.realtime.checkpoint import checkpoint_once
from app.realtime.crdt import doc_to_text, new_doc
from app.realtime.manager import RoomManager, room_manager


async def test_redis_outage_returns_503_and_records_failed_run(client, project, db, monkeypatch):
    monkeypatch.setattr(executions, "enqueue_execution", AsyncMock(side_effect=RedisConnectionError("offline")))
    monkeypatch.setattr(executions, "remove_from_queue", AsyncMock(side_effect=RedisConnectionError("offline")))
    response = await client.post(f"/projects/{project['id']}/executions",
        json={"file_id": project["file_id"]},
        headers={"Authorization": f"Bearer {project['owner']['token']}"})
    assert response.status_code == 503
    run = (await db.execute(select(Execution))).scalar_one()
    assert run.status == ExecutionStatus.FAILED
    events = (await db.execute(select(ExecutionEvent).order_by(ExecutionEvent.id))).scalars().all()
    assert [e.event_type for e in events] == ["queued", "failed"]
    assert events[-1].payload["from"] == "queued"
    assert events[-1].payload["by"] == "api"


async def test_stuck_run_is_swept_logged_and_its_containers_removed(db, project, monkeypatch):
    run = Execution(project_id=project["id"], file_id=project["file_id"],
                    status=ExecutionStatus.RUNNING,
                    started_at=datetime.now(UTC) - orphan_sweep.ORPHAN_THRESHOLD - timedelta(seconds=1))
    db.add(run)
    await db.commit()
    kill = Mock()
    monkeypatch.setattr(orphan_sweep.sandbox, "kill_execution", kill)
    monkeypatch.setattr(orphan_sweep, "signal_cancel", AsyncMock())
    assert await orphan_sweep.sweep_once(db) == 1
    await db.refresh(run)
    assert run.status == ExecutionStatus.FAILED
    kill.assert_called_once_with(run.id)
    event = (await db.execute(select(ExecutionEvent))).scalar_one()
    assert event.payload["by"] == "sweeper"
    assert event.payload["to"] == "failed"


async def test_backend_restart_restores_last_checkpoint(db, project, monkeypatch):
    room = await room_manager.get_or_create_room(project["id"], project["file_id"])
    room.doc = new_doc("checkpointed text\n")
    await checkpoint_once()
    room.doc = new_doc("edits made after checkpoint\n")
    restarted = RoomManager()
    monkeypatch.setattr(rooms, "room_manager", restarted)
    restored = await rooms.open_room(project["id"], project["file_id"])
    assert restored is not room
    assert doc_to_text(restored.doc) == "checkpointed text\n"
