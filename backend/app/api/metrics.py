"""Metrics endpoints — for the dashboard in Feature 12.

We expose simple, cheap-to-compute aggregates. The metrics dashboard reads
these (and structured logs) to render charts.
"""
from __future__ import annotations

import statistics
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter
from sqlalchemy import func, select

from app.core.deps import DbSession
from app.models import Execution, ExecutionEvent

router = APIRouter(prefix="/metrics", tags=["metrics"])


def _percentile(values: list[float], pct: float) -> float:
    """pct in [0, 100]. Works for any sample size >= 1."""
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    quantiles = statistics.quantiles(values, n=100, method="inclusive")
    idx = min(max(round(pct) - 1, 0), len(quantiles) - 1)
    return quantiles[idx]


@router.get("/executions/summary")
async def execution_summary(db: DbSession) -> dict:
    res = await db.execute(
        select(Execution.status, func.count(Execution.id)).group_by(Execution.status)
    )
    counts = {str(status): count for status, count in res.all()}

    # Runtime (started_at -> finished_at) and queue wait (queued_at ->
    # started_at), for NFR3. Computed in Python from raw timestamp pairs
    # rather than via SQL func.extract("epoch", ...): that function's
    # behavior is dialect-specific (it silently produces garbage under the
    # SQLite test DB, confirmed while adding this endpoint's first tests —
    # nothing had ever exercised this query with actual rows before), and a
    # percentile needs an ordered-set aggregate SQLite doesn't have either.
    res_dur = await db.execute(
        select(Execution.started_at, Execution.finished_at).where(
            Execution.started_at.is_not(None),
            Execution.finished_at.is_not(None),
        )
    )
    runtime_seconds = [
        (finished - started).total_seconds() for started, finished in res_dur.all()
    ]
    avg_runtime_seconds = statistics.fmean(runtime_seconds) if runtime_seconds else 0.0

    res_wait = await db.execute(
        select(Execution.queued_at, Execution.started_at).where(
            Execution.started_at.is_not(None)
        )
    )
    wait_seconds = [
        (started - queued).total_seconds() for queued, started in res_wait.all()
    ]
    queue_wait_avg_seconds = statistics.fmean(wait_seconds) if wait_seconds else 0.0
    queue_wait_p95_seconds = _percentile(wait_seconds, 95)

    return {
        "status_counts": counts,
        "avg_runtime_seconds": round(avg_runtime_seconds, 4),
        "queue_wait_avg_seconds": round(queue_wait_avg_seconds, 4),
        "queue_wait_p95_seconds": round(queue_wait_p95_seconds, 4),
    }


@router.get("/executions/last_hour")
async def executions_last_hour(db: DbSession) -> dict:
    cutoff = datetime.now(UTC) - timedelta(hours=1)
    res = await db.execute(
        select(func.count(Execution.id)).where(Execution.queued_at >= cutoff)
    )
    return {"last_hour": res.scalar() or 0}


@router.get("/events/recent")
async def recent_events(db: DbSession, limit: int = 100) -> dict:
    res = await db.execute(
        select(ExecutionEvent)
        .order_by(ExecutionEvent.id.desc())
        .limit(limit)
    )
    events = [
        {
            "id": e.id,
            "execution_id": e.execution_id,
            "event_type": e.event_type,
            "payload": e.payload,
            "created_at": e.created_at.isoformat(),
        }
        for e in res.scalars().all()
    ]
    return {"events": events}
