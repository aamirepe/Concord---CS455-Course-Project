"""Checked execution transitions and their transactional audit records."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.execution import Execution, ExecutionEvent, ExecutionStatus, can_transition

log = logging.getLogger(__name__)


def record_status_event(
    db: AsyncSession, execution_id: int, previous: ExecutionStatus | None,
    target: ExecutionStatus, *, by: str, **fields: Any,
) -> None:
    """Append to the caller's transaction; never commit a state without its event."""
    if by not in {"api", "worker", "sweeper"}:
        raise ValueError("Unknown execution event actor")
    payload = {key: value.isoformat() if isinstance(value, datetime) else value
               for key, value in fields.items()}
    payload.update({"from": previous.value if previous is not None else None,
                    "to": target.value, "by": by})
    db.add(ExecutionEvent(execution_id=execution_id, event_type=target.value, payload=payload))


async def change_status(
    db: AsyncSession, execution: Execution, target: ExecutionStatus, **fields: Any,
) -> bool:
    """Apply an allowed change with a conditional write, leaving commit to the caller.

    The compare-and-set prevents a stale worker or sweeper from overwriting a
    cancellation committed by another session. A losing writer adds no event.
    """
    by = fields.pop("by", "api")
    if by not in {"api", "worker", "sweeper"}:
        raise ValueError("Unknown execution event actor")
    allowed_fields = {"exit_code", "stdout", "stderr", "started_at", "finished_at", "worker_id"}
    if fields.keys() - allowed_fields:
        raise ValueError("Unsupported execution status metadata")
    current = ExecutionStatus(execution.status)
    target = ExecutionStatus(target)
    if not can_transition(current, target):
        log.warning("Refused execution %d transition %s -> %s", execution.id, current.value, target.value)
        return False
    changed = (await db.execute(
        update(Execution)
        .where(Execution.id == execution.id, Execution.status == current)
        .values(status=target, **fields)
        .returning(Execution.id)
        .execution_options(synchronize_session=False)
    )).scalar_one_or_none()
    await db.refresh(execution)
    if changed is None:
        log.warning("Refused stale execution %d transition %s -> %s", execution.id, current.value, target.value)
        return False
    record_status_event(db, execution.id, current, target, by=by, **fields)
    return True
