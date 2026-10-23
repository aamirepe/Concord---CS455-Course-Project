"""sweep_once marks stale RUNNING/STARTING executions as FAILED.

Regression coverage for the bug found while planning Phase 6's chaos test:
sweep_orphans() (the only recovery mechanism for a worker that dies
mid-execution) existed but was never scheduled from app startup, so FR12
("the system survives a worker crash mid-execution") was not actually met.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from docker.errors import APIError, NotFound
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import app.realtime.orphan_sweep as orphan_sweep_module
from app.core.config import get_settings
from app.db.session import AsyncSessionLocal
from app.models import DebugSession, DebugStatus, Execution, ExecutionEvent, ExecutionStatus
from app.realtime.orphan_sweep import ORPHAN_THRESHOLD, sweep_once


@pytest.fixture(autouse=True)
def docker_client(monkeypatch):
    client = Mock()
    client.containers.list.return_value = []
    client.containers.get.side_effect = NotFound("No such container")
    monkeypatch.setattr(orphan_sweep_module.sandbox, "_client", client)
    return client


@pytest.fixture(autouse=True)
def cancelled(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Record cancel signals instead of publishing to Redis."""
    signalled: list[int] = []

    async def _fake_signal_cancel(execution_id: int) -> None:
        signalled.append(execution_id)

    monkeypatch.setattr(orphan_sweep_module, "signal_cancel", _fake_signal_cancel)
    return signalled


@pytest.mark.asyncio
async def test_sweep_marks_stale_running_execution_as_failed(
    db: AsyncSession, client: AsyncClient, project: dict
) -> None:
    stale = Execution(
        project_id=project["id"],
        file_id=project["file_id"],
        status=ExecutionStatus.RUNNING,
        started_at=datetime.now(UTC) - ORPHAN_THRESHOLD - timedelta(minutes=1),
    )
    db.add(stale)
    await db.commit()
    await db.refresh(stale)

    count = await sweep_once(db)

    assert count == 1
    await db.refresh(stale)
    assert stale.status == ExecutionStatus.FAILED
    assert stale.stderr == "Worker died mid-execution (orphan sweep)"
    assert stale.finished_at is not None


@pytest.mark.asyncio
async def test_sweep_leaves_recent_running_execution_untouched(
    db: AsyncSession, client: AsyncClient, project: dict
) -> None:
    fresh = Execution(
        project_id=project["id"],
        file_id=project["file_id"],
        status=ExecutionStatus.RUNNING,
        started_at=datetime.now(UTC) - timedelta(seconds=10),
    )
    db.add(fresh)
    await db.commit()
    await db.refresh(fresh)

    count = await sweep_once(db)

    assert count == 0
    await db.refresh(fresh)
    assert fresh.status == ExecutionStatus.RUNNING


@pytest.mark.asyncio
async def test_sweep_leaves_queued_execution_untouched(
    db: AsyncSession, client: AsyncClient, project: dict
) -> None:
    queued = Execution(
        project_id=project["id"],
        file_id=project["file_id"],
        status=ExecutionStatus.QUEUED,
    )
    db.add(queued)
    await db.commit()
    await db.refresh(queued)

    count = await sweep_once(db)

    assert count == 0
    await db.refresh(queued)
    assert queued.status == ExecutionStatus.QUEUED


@pytest.mark.asyncio
async def test_sweep_fails_stale_compiling_execution_and_signals_cancel(
    db: AsyncSession, client: AsyncClient, project: dict, cancelled: list[int]
) -> None:
    # A worker that dies mid-compile (C++) used to leave the row stuck forever.
    stale = Execution(
        project_id=project["id"],
        file_id=project["file_id"],
        status=ExecutionStatus.COMPILING,
        started_at=datetime.now(UTC) - ORPHAN_THRESHOLD - timedelta(minutes=1),
    )
    db.add(stale)
    await db.commit()
    await db.refresh(stale)

    count = await sweep_once(db)

    assert count == 1
    assert cancelled == [stale.id]
    await db.refresh(stale)
    assert stale.status == ExecutionStatus.FAILED


@pytest.mark.asyncio
async def test_sweep_keeps_db_change_when_cancel_signal_fails(
    db: AsyncSession, client: AsyncClient, project: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _redis_down(execution_id: int) -> None:
        raise ConnectionError("redis unavailable")

    monkeypatch.setattr(orphan_sweep_module, "signal_cancel", _redis_down)
    stale = Execution(
        project_id=project["id"],
        file_id=project["file_id"],
        status=ExecutionStatus.RUNNING,
        started_at=datetime.now(UTC) - ORPHAN_THRESHOLD - timedelta(minutes=1),
    )
    db.add(stale)
    await db.commit()
    await db.refresh(stale)

    count = await sweep_once(db)

    assert count == 1
    await db.refresh(stale)
    assert stale.status == ExecutionStatus.FAILED


async def _debug_execution(db, project, age, debug_status=DebugStatus.ACTIVE):
    execution = Execution(
        project_id=project["id"], file_id=project["file_id"],
        status=ExecutionStatus.RUNNING, started_at=datetime.now(UTC) - age,
    )
    db.add(execution)
    await db.flush()
    db.add(DebugSession(execution_id=execution.id, project_id=project["id"],
                        started_by=project["owner"]["id"], status=debug_status))
    await db.commit()
    return execution


@pytest.mark.asyncio
@pytest.mark.parametrize("debug_status", [DebugStatus.ACTIVE, DebugStatus.PAUSED])
async def test_sweep_preserves_live_debug_session_beyond_five_minutes(
    db, project, cancelled, debug_status,
):
    execution = await _debug_execution(db, project, timedelta(minutes=6), debug_status)
    assert await sweep_once(db) == 0
    await db.refresh(execution)
    assert execution.status == ExecutionStatus.RUNNING
    assert execution.finished_at is None
    assert cancelled == []


@pytest.mark.asyncio
async def test_sweep_respects_configured_debug_timeout(db, project, cancelled, monkeypatch):
    monkeypatch.setattr(get_settings(), "sandbox_debug_timeout_seconds", 20 * 60)
    execution = await _debug_execution(db, project, timedelta(minutes=16))
    assert await sweep_once(db) == 0
    await db.refresh(execution)
    assert execution.status == ExecutionStatus.RUNNING
    assert cancelled == []


@pytest.mark.asyncio
async def test_sweep_recovers_debug_run_after_timeout_and_recovery_grace(db, project, cancelled):
    age = timedelta(seconds=get_settings().sandbox_debug_timeout_seconds) + ORPHAN_THRESHOLD + timedelta(minutes=1)
    execution = await _debug_execution(db, project, age)
    assert await sweep_once(db) == 1
    await db.refresh(execution)
    assert execution.status == ExecutionStatus.FAILED
    assert cancelled == [execution.id]


@pytest.mark.asyncio
async def test_stopped_debug_session_does_not_protect_stale_execution(db, project, cancelled):
    execution = await _debug_execution(db, project, timedelta(minutes=6), DebugStatus.STOPPED)
    assert await sweep_once(db) == 1
    await db.refresh(execution)
    assert execution.status == ExecutionStatus.FAILED
    assert cancelled == [execution.id]


async def test_sweeper_appends_failed_event_in_same_transaction(db, project):
    run = Execution(project_id=project["id"], file_id=project["file_id"],
                    status=ExecutionStatus.RUNNING,
                    started_at=datetime.now(UTC) - ORPHAN_THRESHOLD - timedelta(seconds=1))
    db.add(run)
    await db.commit()
    assert await sweep_once(db) == 1
    event = (await db.execute(select(ExecutionEvent).where(
        ExecutionEvent.execution_id == run.id))).scalar_one()
    assert event.event_type == "failed"
    assert event.payload["from"] == "running"
    assert event.payload["to"] == "failed"
    assert event.payload["by"] == "sweeper"
    assert await sweep_once(db) == 0


@pytest.mark.parametrize("prefix", ["concord-exec", "concord-build"])
@pytest.mark.parametrize("redis_down", [False, True])
async def test_sweep_retries_failed_container_removal_after_restart(
    db, project, docker_client, monkeypatch, prefix, redis_down,
):
    run = Execution(
        project_id=project["id"], file_id=project["file_id"],
        status=ExecutionStatus.RUNNING,
        started_at=datetime.now(UTC) - ORPHAN_THRESHOLD - timedelta(seconds=1),
    )
    db.add(run)
    await db.commit()
    name = f"{prefix}-{run.id}"
    container = SimpleNamespace(name=name, remove=Mock())
    remaining = {name: container}

    def get_container(target):
        if target not in remaining:
            raise NotFound("No such container")
        return remaining[target]

    def remove_container(*, force):
        assert force
        if container.remove.call_count == 1:
            raise APIError("Docker temporarily unavailable")
        del remaining[name]

    container.remove.side_effect = remove_container
    docker_client.containers.get.side_effect = get_container
    docker_client.containers.list.side_effect = lambda **kwargs: list(remaining.values())
    signal = AsyncMock(side_effect=ConnectionError("Redis unavailable") if redis_down else None)
    monkeypatch.setattr(orphan_sweep_module, "signal_cancel", signal)

    assert await sweep_once(db) == 1
    await db.refresh(run)
    assert run.status == ExecutionStatus.FAILED
    finished_at = run.finished_at
    assert name in remaining
    assert container.remove.call_count == 1

    # A fresh session has no memory of the failed Docker request.
    async with AsyncSessionLocal() as restarted_db:
        assert await sweep_once(restarted_db) == 0
        assert remaining == {}
        assert await sweep_once(restarted_db) == 0
    assert container.remove.call_count == 2
    signal.assert_awaited_once_with(run.id)
    await db.refresh(run)
    assert run.status == ExecutionStatus.FAILED
    assert run.finished_at == finished_at
    events = (await db.execute(select(ExecutionEvent).where(
        ExecutionEvent.execution_id == run.id))).scalars().all()
    assert len(events) == 1
    assert events[0].payload["by"] == "sweeper"


async def test_sweep_retries_terminal_cleanup_after_container_listing_recovers(
    db, project, docker_client,
):
    run = Execution(project_id=project["id"], file_id=project["file_id"], status=ExecutionStatus.FAILED)
    db.add(run)
    await db.commit()
    container = SimpleNamespace(name=f"concord-exec-{run.id}", remove=Mock())
    docker_client.containers.list.side_effect = [APIError("Docker offline"), [container]]

    def get_container(name):
        if name == container.name:
            return container
        raise NotFound("No such container")

    docker_client.containers.get.side_effect = get_container

    assert await sweep_once(db) == 0
    container.remove.assert_not_called()
    async with AsyncSessionLocal() as restarted_db:
        assert await sweep_once(restarted_db) == 0
    container.remove.assert_called_once_with(force=True)


async def test_terminal_reconciliation_preserves_live_and_unrelated_containers(
    db, project, docker_client, monkeypatch,
):
    runs = [Execution(
        project_id=project["id"], file_id=project["file_id"], status=status,
        started_at=datetime.now(UTC),
    ) for status in ExecutionStatus]
    db.add_all(runs)
    await db.commit()
    live_debug = await _debug_execution(db, project, timedelta(minutes=6), DebugStatus.PAUSED)
    names = [f"{prefix}-{run.id}" for run in [*runs, live_debug]
             for prefix in ("concord-exec", "concord-build")]
    names += ["unrelated-container", "concord-exec-999999", "concord-exec-1-extra",
              "concord-build-0", "concord-exec-9999999999999999999999"]
    docker_client.containers.list.return_value = [SimpleNamespace(name=name) for name in names]
    kill = Mock()
    monkeypatch.setattr(orphan_sweep_module.sandbox, "kill_execution", kill)
    assert await sweep_once(db) == 0
    terminal = {ExecutionStatus.COMPLETED, ExecutionStatus.FAILED,
                ExecutionStatus.TIMEOUT, ExecutionStatus.CANCELLED}
    assert {call.args[0] for call in kill.call_args_list} == {
        run.id for run in runs if run.status in terminal
    }
    assert kill.call_count == len(terminal)
    await db.refresh(live_debug)
    assert live_debug.status == ExecutionStatus.RUNNING
