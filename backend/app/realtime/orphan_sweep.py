"""Orphan sweep loop for detecting and handling crashed workers.

Periodically checks for executions stuck in STARTING, COMPILING or RUNNING
state when their associated worker has likely crashed, marks them as FAILED,
and signals cancellation so a still-alive worker kills its container.
"""

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.session import AsyncSessionLocal
from app.models import DebugSession, DebugStatus, Execution, ExecutionStatus
from app.realtime.execution_queue import signal_cancel

log = logging.getLogger(__name__)

ORPHAN_THRESHOLD = timedelta(minutes=5)

ACTIVE_STATUSES = (
    ExecutionStatus.STARTING.value,
    ExecutionStatus.COMPILING.value,
    ExecutionStatus.RUNNING.value,
)


async def sweep_once(db: AsyncSession) -> int:
    """Mark executions stuck in STARTING/COMPILING/RUNNING past the threshold as FAILED.

    Returns the number of executions marked failed.
    """
    now = datetime.now(UTC)
    threshold_time = now - ORPHAN_THRESHOLD
    # The debugger has a longer total lifetime than a normal run. Give it
    # that configured lifetime plus the normal recovery window for startup
    # and teardown; stepping affects its idle timer, not this total limit.
    debug_threshold_time = threshold_time - timedelta(seconds=get_settings().sandbox_debug_timeout_seconds)
    active_debug = select(DebugSession.id).where(
        DebugSession.execution_id == Execution.id,
        DebugSession.status.in_((DebugStatus.ACTIVE, DebugStatus.PAUSED)),
    ).exists()

    stmt = select(Execution).where(
        Execution.status.in_(ACTIVE_STATUSES),
        Execution.started_at < threshold_time,
        or_(~active_debug, Execution.started_at < debug_threshold_time),
    )
    result = await db.execute(stmt)
    orphaned_executions = result.scalars().all()

    for execution in orphaned_executions:
        log.warning(
            "Found orphaned execution %d (status: %s, started: %s), "
            "marking as failed due to worker death",
            execution.id, execution.status, execution.started_at
        )
        execution.status = ExecutionStatus.FAILED
        execution.finished_at = datetime.now(UTC)
        execution.stderr = "Worker died mid-execution (orphan sweep)"
        db.add(execution)

    if orphaned_executions:
        await db.commit()
        log.info("Marked %d orphaned executions as failed", len(orphaned_executions))

    # Best effort: if the worker is alive but stuck, this makes it kill the
    # container. A Redis outage must not undo the DB state change above.
    for execution in orphaned_executions:
        try:
            await signal_cancel(execution.id)
        except Exception as e:
            log.warning("Could not signal cancel for execution %d: %s", execution.id, e)

    return len(orphaned_executions)


async def sweep_orphans() -> None:
    """Background task that sweeps for orphaned executions every 60 seconds."""
    while True:
        try:
            await asyncio.sleep(60)
            async with AsyncSessionLocal() as db:
                await sweep_once(db)
        except Exception as e:
            log.error("Error in orphan sweep: %s", e, exc_info=True)
            await asyncio.sleep(60)
