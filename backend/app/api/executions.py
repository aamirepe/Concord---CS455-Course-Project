"""Execution endpoints: trigger, fetch status, cancel."""
from __future__ import annotations

import json
import logging
import socket
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, status
from redis.exceptions import RedisError
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.deps import CurrentUser, DbSession, get_member
from app.debug import control
from app.models import (
    Breakpoint,
    DebugSession,
    DebugStatus,
    Execution,
    ExecutionStatus,
    File,
    ProjectMember,
    Role,
    User,
)
from app.realtime.execution_queue import enqueue_execution, remove_from_queue
from app.realtime.manager import room_manager
from app.realtime.source import get_file_text
from app.runtime.registry import PLAINTEXT
from app.schemas import ExecutionOut, RunRequest
from app.services.execution_state import change_status, record_status_event

router = APIRouter(prefix="/projects/{project_id}/executions", tags=["executions"])
log = logging.getLogger(__name__)

FINISHED_STATUSES = {ExecutionStatus.COMPLETED, ExecutionStatus.FAILED, ExecutionStatus.TIMEOUT, ExecutionStatus.CANCELLED}


async def _require_runner(db: AsyncSession, project_id: int, user_id: int) -> ProjectMember:
    member = await get_member(db, project_id, user_id)
    if Role(member.role) not in {Role.OWNER, Role.EDITOR, Role.DEBUGGER}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Requires editor+ role to run code"
        )
    return member


@router.post("", response_model=ExecutionOut, status_code=status.HTTP_202_ACCEPTED)
async def run_code(
    project_id: int, payload: RunRequest, user: CurrentUser, db: DbSession
) -> ExecutionOut:
    return await start_run(db, project_id, user, payload.file_id, stdin=payload.stdin, debug=payload.debug)


async def start_run(
    db: AsyncSession, project_id: int, user: User, file_id: int, *, stdin: str | None = None, debug: bool = False
) -> ExecutionOut:
    """Queue a run (or a debug session) of ``file_id`` for the worker."""
    member = await _require_runner(db, project_id, user.id)

    file_obj = await db.get(File, file_id)
    if not file_obj:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")
    if file_obj.project_id != project_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File does not belong to this project",
        )
    if file_obj.language == PLAINTEXT:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{file_obj.path} can't be run: only Python, JavaScript and C/C++ files can",
        )
    if debug:
        _check_can_debug(member, file_obj)

    source_text = await get_file_text(db, project_id, file_id)

    execution = Execution(
        project_id=project_id,
        file_id=file_id,
        status=ExecutionStatus.QUEUED,
        queued_at=datetime.now(UTC),
    )
    db.add(execution)
    await db.flush()

    record_status_event(db, execution.id, None, ExecutionStatus.QUEUED, by="api")

    debug_session_info = None
    if debug:
        await _claim_debug_slot(db, project_id, execution, file_obj, user)
        db.add(DebugSession(
            execution_id=execution.id, project_id=project_id, started_by=user.id,
            current_file=file_obj.path, status=DebugStatus.ACTIVE,
        ))
        debug_session_info = await _debug_info(db, project_id, user)

    await db.commit()
    await db.refresh(execution)

    try:
        await enqueue_execution(
            execution_id=execution.id,
            project_id=project_id,
            file_id=file_id,
            source_text=source_text,
            worker_id_hint=socket.gethostname(),
            debug=debug,
            debug_session_info=debug_session_info,
            language=file_obj.language,
            stdin=None if debug else stdin,
            role=member.role,
        )
    except (RedisError, OSError) as exc:
        compensated = await _fail_undelivered_run(db, project_id, execution.id, debug)
        if compensated:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Execution queue unavailable; please try again",
            ) from exc
        # Delivery succeeded and a worker claimed the run before the error
        # reached us. Preserve its state rather than failing a live execution.
        await db.refresh(execution)
        return ExecutionOut.model_validate(execution)
    # Lets everyone's console say who started this run and on which file.
    room_manager.broadcast_to_project(project_id, json.dumps({
        "op": "EXECUTION_QUEUED",
        "execution_id": execution.id,
        "file_id": file_id,
        "path": file_obj.path,
        "user_id": user.id,
        "username": user.username,
        "debug": debug,
    }))
    return ExecutionOut.model_validate(execution)


async def _fail_undelivered_run(db: AsyncSession, project_id: int, execution_id: int, debug: bool) -> bool:
    """Compete with the worker's queued-to-starting claim before compensating."""
    finished_at = datetime.now(UTC)
    execution = await db.get(Execution, execution_id, populate_existing=True)
    if execution is None or execution.status != ExecutionStatus.QUEUED or not await change_status(
        db, execution, ExecutionStatus.FAILED, by="api", finished_at=finished_at,
        stderr="Execution queue delivery failed; please try again",
    ):
        await db.rollback()
        return False
    if debug:
        await db.execute(
            update(DebugSession).where(DebugSession.execution_id == execution_id)
            .values(status=DebugStatus.STOPPED, ended_at=finished_at)
        )
    # Commit before Redis cleanup: a continuing outage must not undo recovery.
    await db.commit()
    try:
        await remove_from_queue(execution_id)
    except (RedisError, OSError):
        log.warning("Could not remove failed execution %d from Redis", execution_id, exc_info=True)
    if debug:
        try:
            await control.clear_active(project_id, execution_id)
        except (RedisError, OSError):
            log.warning("Could not clear failed debug execution %d from Redis", execution_id, exc_info=True)
    return True


def _check_can_debug(member: ProjectMember, file_obj: File) -> None:
    if not get_settings().enable_debugger:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The debugger is turned off on this server (ENABLE_DEBUGGER)",
        )
    if Role(member.role) not in {Role.OWNER, Role.DEBUGGER}:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Debugging needs the debugger role")
    if file_obj.language != "python":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Only Python programs can be debugged",
        )


async def _claim_debug_slot(db: AsyncSession, project_id: int, execution: Execution, file_obj: File, user: User) -> None:
    """One debug session per project at a time (they share the breakpoints)."""
    info = {
        "execution_id": execution.id, "file_id": file_obj.id, "path": file_obj.path,
        "user_id": user.id, "username": user.username,
    }
    try:
        if await control.claim_active(project_id, info):
            return
        current = await control.get_active(project_id)
        running = await db.get(Execution, current["execution_id"]) if current else None
        if running is None or running.status in FINISHED_STATUSES:
            await control.set_active(project_id, info)  # left over from a crashed worker
            return
    except (RedisError, OSError) as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="The debugger is unavailable (no Redis)",
        ) from e
    who = current or {"username": "someone", "path": "a file"}  # it ended while we looked
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=f"{who['username']} is already debugging {who['path']}; stop that session first",
    )


async def _debug_info(db: AsyncSession, project_id: int, user: User) -> dict:
    """Every enabled breakpoint in the project, and each file's id by path."""
    files = (await db.execute(select(File.id, File.path).where(File.project_id == project_id))).all()
    rows = (await db.execute(
        select(File.path, Breakpoint.line)
        .join(Breakpoint, Breakpoint.file_id == File.id)
        .where(File.project_id == project_id, Breakpoint.enabled.is_(True))
    )).all()
    breakpoints: dict[str, list[int]] = {}
    for path, line in rows:
        breakpoints.setdefault(path, []).append(line)
    return {
        "breakpoints": breakpoints,
        "file_ids": {path: fid for fid, path in files},
        "user_id": user.id,
        "username": user.username,
    }


@router.get("", response_model=list[ExecutionOut])
async def list_executions(
    project_id: int, user: CurrentUser, db: DbSession
) -> list[ExecutionOut]:
    await get_member(db, project_id, user.id)
    res = await db.execute(
        select(Execution)
        .where(Execution.project_id == project_id)
        .order_by(Execution.id.desc())
        .limit(100)
    )
    return [ExecutionOut.model_validate(e) for e in res.scalars().all()]


@router.get("/{execution_id}", response_model=ExecutionOut)
async def get_execution(
    project_id: int, execution_id: int, user: CurrentUser, db: DbSession
) -> ExecutionOut:
    await get_member(db, project_id, user.id)
    execution = await db.get(Execution, execution_id)
    if not execution or execution.project_id != project_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Execution not found")
    return ExecutionOut.model_validate(execution)


@router.post("/{execution_id}/cancel", response_model=ExecutionOut)
async def cancel_execution(
    project_id: int, execution_id: int, user: CurrentUser, db: DbSession
) -> ExecutionOut:
    member = await get_member(db, project_id, user.id)
    if Role(member.role) not in {Role.OWNER, Role.EDITOR, Role.DEBUGGER}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Requires editor+ role to cancel"
        )

    execution = await db.get(Execution, execution_id)
    if not execution or execution.project_id != project_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Execution not found")

    if execution.status in {
        ExecutionStatus.COMPLETED,
        ExecutionStatus.FAILED,
        ExecutionStatus.TIMEOUT,
        ExecutionStatus.CANCELLED,
    }:
        return ExecutionOut.model_validate(execution)

    if not await change_status(
        db, execution, ExecutionStatus.CANCELLED, by="api", finished_at=datetime.now(UTC),
    ):
        await db.refresh(execution)
        return ExecutionOut.model_validate(execution)
    await db.commit()
    await db.refresh(execution)

    # If a worker already picked this one up, signal it via Redis so it kills
    # the container. If it's still only waiting in the queue, drop it there
    # directly so it never costs a slot at all.
    from app.realtime.execution_queue import remove_from_queue, signal_cancel

    await remove_from_queue(execution_id)
    await signal_cancel(execution_id)
    return ExecutionOut.model_validate(execution)
