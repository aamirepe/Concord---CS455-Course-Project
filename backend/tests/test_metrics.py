"""Coverage for /metrics/executions/summary — previously untested.

Covers the pre-existing avg_runtime_seconds field and the queue-wait fields
added for NFR3 (queue wait <1s p95), which had no backing metric before.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Execution, ExecutionStatus


@pytest.mark.asyncio
async def test_summary_on_empty_db_returns_zeros(client: AsyncClient) -> None:
    r = await client.get("/metrics/executions/summary")
    assert r.status_code == 200
    body = r.json()
    assert body["status_counts"] == {}
    assert body["avg_runtime_seconds"] == 0.0
    assert body["queue_wait_avg_seconds"] == 0.0
    assert body["queue_wait_p95_seconds"] == 0.0


@pytest.mark.asyncio
async def test_summary_computes_avg_runtime_and_queue_wait(
    db: AsyncSession, client: AsyncClient, project: dict
) -> None:
    now = datetime.now(UTC)

    # queue wait 2s, runtime 10s
    e1 = Execution(
        project_id=project["id"],
        file_id=project["file_id"],
        status=ExecutionStatus.COMPLETED,
        queued_at=now,
        started_at=now + timedelta(seconds=2),
        finished_at=now + timedelta(seconds=12),
    )
    # queue wait 4s, runtime 20s
    e2 = Execution(
        project_id=project["id"],
        file_id=project["file_id"],
        status=ExecutionStatus.COMPLETED,
        queued_at=now,
        started_at=now + timedelta(seconds=4),
        finished_at=now + timedelta(seconds=24),
    )
    db.add_all([e1, e2])
    await db.commit()

    r = await client.get("/metrics/executions/summary")
    assert r.status_code == 200
    body = r.json()

    assert body["status_counts"] == {"completed": 2}
    assert body["avg_runtime_seconds"] == pytest.approx(15.0, abs=0.01)
    assert body["queue_wait_avg_seconds"] == pytest.approx(3.0, abs=0.01)
    # p95 of [2, 4] via statistics.quantiles(n=100, method="inclusive") lands
    # very close to the max of a 2-sample set.
    assert body["queue_wait_p95_seconds"] == pytest.approx(4.0, abs=0.2)


@pytest.mark.asyncio
async def test_summary_excludes_executions_never_started(
    db: AsyncSession, client: AsyncClient, project: dict
) -> None:
    queued_only = Execution(
        project_id=project["id"],
        file_id=project["file_id"],
        status=ExecutionStatus.QUEUED,
    )
    db.add(queued_only)
    await db.commit()

    r = await client.get("/metrics/executions/summary")
    assert r.status_code == 200
    body = r.json()

    assert body["status_counts"] == {"queued": 1}
    assert body["queue_wait_avg_seconds"] == 0.0
    assert body["queue_wait_p95_seconds"] == 0.0
