"""One debug session per project, even when several members start one at once (FR-18, NFR-D3).

Debug sessions share a project's breakpoints, so a second one must be refused
while the first runs. ``_claim_debug_slot`` takes the slot with a single
SET NX, and a start that loses gets 409. This races many starts in one project
and checks that exactly one wins and every other gets 409. It calls the claim
step directly with a database session per start: the tests' in-memory SQLite
shares a single connection, so concurrent HTTP requests would also share (and
roll back) each other's transactions, which no deployment does.
"""
import asyncio
from datetime import UTC, datetime

import pytest
from fastapi import HTTPException

from app.api.executions import _claim_debug_slot
from app.db.session import AsyncSessionLocal
from app.debug import control
from app.models import Execution, ExecutionStatus, File, User

STARTS = 12


class RacingRedis:
    """Yields to the event loop before each command so the starts interleave.
    Each command is then atomic, as it is on a real Redis."""

    def __init__(self, inner) -> None:
        self.inner = inner

    async def set(self, *args, **kwargs):
        await asyncio.sleep(0)
        return await self.inner.set(*args, **kwargs)

    async def get(self, *args, **kwargs):
        await asyncio.sleep(0)
        return await self.inner.get(*args, **kwargs)


@pytest.mark.asyncio
async def test_concurrent_debug_starts_in_one_project_give_exactly_one_session(
    db, project, fake_redis, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(control, "_redis", lambda: RacingRedis(fake_redis))
    project_id = project["id"]
    file_obj = await db.get(File, project["file_id"])
    user = await db.get(User, project["owner"]["id"])

    executions = [
        Execution(project_id=project_id, file_id=file_obj.id, status=ExecutionStatus.QUEUED, queued_at=datetime.now(UTC))
        for _ in range(STARTS)
    ]
    db.add_all(executions)
    await db.commit()

    async def start(execution: Execution) -> HTTPException | None:
        async with AsyncSessionLocal() as session:
            try:
                await _claim_debug_slot(session, project_id, execution, file_obj, user)
            except HTTPException as refused:
                return refused
            return None

    outcomes = await asyncio.gather(*(start(execution) for execution in executions))

    winners = [e for e, refused in zip(executions, outcomes, strict=True) if refused is None]
    refusals = [refused for refused in outcomes if refused is not None]
    assert len(winners) == 1
    assert [r.status_code for r in refusals] == [409] * (STARTS - 1)
    assert all("already debugging main.py" in r.detail for r in refusals)
    assert (await control.get_active(project_id))["execution_id"] == winners[0].id
