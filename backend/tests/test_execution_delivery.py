"""A rejected queue delivery must not leave a durable, unclaimable queued run."""
from unittest.mock import AsyncMock, Mock

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy import select

from app.api import executions
from app.db.session import AsyncSessionLocal
from app.debug import control
from app.models import DebugSession, DebugStatus, Execution, ExecutionStatus


@pytest.mark.asyncio
@pytest.mark.parametrize("debug", [False, True])
@pytest.mark.parametrize("error", [RedisConnectionError, OSError])
async def test_queue_failure_persists_failed_run_and_releases_debug_claim(
    client, project, fake_redis, monkeypatch, debug, error,
):
    monkeypatch.setattr(executions, "enqueue_execution", AsyncMock(side_effect=error("queue unavailable")))
    monkeypatch.setattr(executions, "remove_from_queue", AsyncMock())
    broadcast = Mock()
    monkeypatch.setattr(executions.room_manager, "broadcast_to_project", broadcast)
    response = await client.post(
        f"/projects/{project['id']}/executions",
        json={"file_id": project["file_id"], "debug": debug},
        headers={"Authorization": f"Bearer {project['owner']['token']}"},
    )
    assert response.status_code == 503
    assert response.json()["detail"] == "Execution queue unavailable; please try again"
    async with AsyncSessionLocal() as verification:
        run = (await verification.execute(select(Execution))).scalar_one()
        assert run.status == ExecutionStatus.FAILED
        assert run.finished_at is not None
        assert run.started_at is None
        assert "queue" in run.stderr.lower()
        session = (await verification.execute(select(DebugSession))).scalar_one_or_none()
        if debug:
            assert session.status == DebugStatus.STOPPED
            assert session.ended_at is not None
        else:
            assert session is None
    assert await control.get_active(project["id"]) is None
    broadcast.assert_not_called()


@pytest.mark.asyncio
async def test_queue_failure_cleanup_outage_does_not_hide_persisted_failure(
    client, project, fake_redis, monkeypatch,
):
    monkeypatch.setattr(executions, "enqueue_execution", AsyncMock(side_effect=RedisConnectionError("offline")))
    monkeypatch.setattr(executions, "remove_from_queue", AsyncMock(side_effect=RedisConnectionError("offline")))
    monkeypatch.setattr(control, "clear_active", AsyncMock(side_effect=RedisConnectionError("offline")))
    response = await client.post(
        f"/projects/{project['id']}/executions",
        json={"file_id": project["file_id"], "debug": True},
        headers={"Authorization": f"Bearer {project['owner']['token']}"},
    )
    assert response.status_code == 503
    async with AsyncSessionLocal() as verification:
        run = (await verification.execute(select(Execution))).scalar_one()
        session = (await verification.execute(select(DebugSession))).scalar_one()
        assert run.status == ExecutionStatus.FAILED
        assert session.status == DebugStatus.STOPPED


@pytest.mark.asyncio
async def test_lost_delivery_ack_does_not_fail_a_run_already_claimed_by_worker(
    client, project, fake_redis, monkeypatch,
):
    async def claimed_then_connection_lost(**payload):
        async with AsyncSessionLocal() as worker_db:
            run = await worker_db.get(Execution, payload["execution_id"])
            run.status = ExecutionStatus.STARTING
            await worker_db.commit()
        raise RedisConnectionError("ack lost after delivery")

    monkeypatch.setattr(executions, "enqueue_execution", claimed_then_connection_lost)
    clear = AsyncMock()
    remove = AsyncMock()
    monkeypatch.setattr(control, "clear_active", clear)
    monkeypatch.setattr(executions, "remove_from_queue", remove)
    response = await client.post(
        f"/projects/{project['id']}/executions",
        json={"file_id": project["file_id"], "debug": True},
        headers={"Authorization": f"Bearer {project['owner']['token']}"},
    )
    assert response.status_code == 202
    assert response.json()["status"] == "starting"
    async with AsyncSessionLocal() as verification:
        run = (await verification.execute(select(Execution))).scalar_one()
        session = (await verification.execute(select(DebugSession))).scalar_one()
        assert run.status == ExecutionStatus.STARTING
        assert run.finished_at is None
        assert session.status == DebugStatus.ACTIVE
    clear.assert_not_called()
    remove.assert_not_called()
