"""sweep_once marks stale RUNNING/STARTING executions as FAILED.

Regression coverage for the bug found while planning Phase 6's chaos test:
sweep_orphans() (the only recovery mechanism for a worker that dies
mid-execution) existed but was never scheduled from app startup, so FR12
("the system survives a worker crash mid-execution") was not actually met.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import app.realtime.orphan_sweep as orphan_sweep_module
import pytest
from app.core.config import get_settings
from app.models import DebugSession, DebugStatus, Execution, ExecutionStatus
from app.realtime.orphan_sweep import ORPHAN_THRESHOLD, sweep_once
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession


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
