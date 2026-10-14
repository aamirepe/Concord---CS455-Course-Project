"""Orphan sweep loop for detecting and handling crashed workers.

Periodically checks for executions stuck in STARTING, COMPILING or RUNNING
state when their associated worker has likely crashed, marks them as FAILED,
and signals cancellation so a still-alive worker kills its container.
"""

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import AsyncSessionLocal
from app.models import Execution, ExecutionStatus
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
    threshold_time = datetime.now(UTC) - ORPHAN_THRESHOLD

    stmt = select(Execution).where(
        Execution.status.in_(ACTIVE_STATUSES),
        Execution.started_at < threshold_time,
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
