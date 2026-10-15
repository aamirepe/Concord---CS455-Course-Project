"""Breakpoints and the collaborative debugger.

A debug session is a run started with ``debug: true`` (see executions.py);
the worker runs it. These endpoints manage breakpoints, report the running
session (so someone who joins mid-session sees where it is paused), and relay
step commands to the worker. Everyone in the project sees the session's
``debug_*`` events; stepping needs the debugger role.
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field
from redis.exceptions import RedisError
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.executions import start_run
from app.core.deps import CurrentUser, DbSession, get_member
from app.debug import control
from app.models import Breakpoint, File, ProjectMember, Role
from app.realtime.execution_queue import publish_project_event
from app.schemas import BreakpointOut, BreakpointUpsert, ExecutionOut

router = APIRouter(prefix="/projects/{project_id}", tags=["debug"])

MAX_BREAKPOINTS_PER_FILE = 200


async def _require_debugger(db: AsyncSession, project_id: int, user_id: int) -> ProjectMember:
    member = await get_member(db, project_id, user_id)
    if Role(member.role) not in {Role.OWNER, Role.DEBUGGER}:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Requires the debugger role")
    return member


async def _file_in_project(db: AsyncSession, project_id: int, file_id: int) -> File:
    file = await db.get(File, file_id)
    if not file or file.project_id != project_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")
    return file


async def _replace(db: AsyncSession, file_id: int, lines: list[tuple[int, bool]], user_id: int) -> list[Breakpoint]:
    await db.execute(delete(Breakpoint).where(Breakpoint.file_id == file_id))
    rows = [Breakpoint(file_id=file_id, line=line, enabled=enabled, created_by=user_id) for line, enabled in lines]
    db.add_all(rows)
    return rows


async def _announce(project_id: int, files: dict[int, File], rows: list[Breakpoint]) -> None:
    """Tell everyone, and a running debug session, about the new breakpoints."""
    lines_by_file: dict[int, list[int]] = {fid: [] for fid in files}
    for row in rows:
        if row.enabled:
            lines_by_file[row.file_id].append(row.line)
    try:
        await publish_project_event(project_id, {"type": "breakpoints_updated", "lines_by_file": lines_by_file})
        active = await control.get_active(project_id)
        if active:
            for fid, lines in lines_by_file.items():
                await control.send_command(active["execution_id"], {
                    "command": "setBreakpoints", "path": files[fid].path, "lines": sorted(set(lines)),
                })
    except (RedisError, OSError):
        pass  # saved; others see the change when they reopen the file


@router.get("/breakpoints", response_model=list[BreakpointOut])
async def list_breakpoints(
    project_id: int, file_id: int, user: CurrentUser, db: DbSession
) -> list[BreakpointOut]:
    await get_member(db, project_id, user.id)
    await _file_in_project(db, project_id, file_id)
    res = await db.execute(select(Breakpoint).where(Breakpoint.file_id == file_id).order_by(Breakpoint.line))
    return [BreakpointOut.model_validate(b) for b in res.scalars().all()]


class FileBreakpoints(BaseModel):
    lines: list[int] = Field(default_factory=list, max_length=MAX_BREAKPOINTS_PER_FILE)


@router.put("/files/{file_id}/breakpoints", response_model=list[BreakpointOut])
async def set_file_breakpoints(
    project_id: int, file_id: int, payload: FileBreakpoints, user: CurrentUser, db: DbSession
) -> list[BreakpointOut]:
    """Replace a file's breakpoints with ``lines`` (``[]`` clears them)."""
    await _require_debugger(db, project_id, user.id)
    file = await _file_in_project(db, project_id, file_id)
    lines = sorted({n for n in payload.lines if n >= 1})
    rows = await _replace(db, file_id, [(n, True) for n in lines], user.id)
    await db.commit()
    for row in rows:
        await db.refresh(row)
    await _announce(project_id, {file_id: file}, rows)
    return [BreakpointOut.model_validate(r) for r in rows]


@router.put("/breakpoints", response_model=list[BreakpointOut])
async def upsert_breakpoints(
    project_id: int,
    payload: list[BreakpointUpsert],
    user: CurrentUser,
    db: DbSession,
    file_id: list[int] = Query(default_factory=list, description="also clear these files when they have no entries"),
) -> list[BreakpointOut]:
    """Older form: replace the breakpoints of every file named in the body (or in ``file_id``)."""
    await _require_debugger(db, project_id, user.id)
    files = {fid: await _file_in_project(db, project_id, fid) for fid in {bp.file_id for bp in payload} | set(file_id)}
    rows: list[Breakpoint] = []
    for fid in files:
        entries = [(bp.line, bp.enabled) for bp in payload if bp.file_id == fid and bp.line >= 1]
        rows += await _replace(db, fid, entries, user.id)
    await db.commit()
    for row in rows:
        await db.refresh(row)
    if files:
        await _announce(project_id, files, rows)
    return [BreakpointOut.model_validate(r) for r in rows]


@router.post("/debug/start", response_model=ExecutionOut, status_code=status.HTTP_202_ACCEPTED)
async def start_debug(project_id: int, file_id: int, user: CurrentUser, db: DbSession) -> ExecutionOut:
    """Start debugging ``file_id``: the same as ``POST /executions`` with ``debug: true``."""
    return await start_run(db, project_id, user, file_id, debug=True)


class DebugStateOut(BaseModel):
    active: dict | None  # {execution_id, file_id, path, user_id, username}
    paused: dict | None  # the last debug_paused event, if the program is paused


@router.get("/debug", response_model=DebugStateOut)
async def debug_state(project_id: int, user: CurrentUser, db: DbSession) -> DebugStateOut:
    """The project's running debug session, for someone who opens the project mid-session."""
    await get_member(db, project_id, user.id)
    try:
        active = await control.get_active(project_id)
        paused = await control.get_state(active["execution_id"]) if active else None
    except (RedisError, OSError):
        return DebugStateOut(active=None, paused=None)
    return DebugStateOut(active=active, paused=paused)


class DebugCommand(BaseModel):
    command: Literal["continue", "next", "stepIn", "stepOut", "pause", "stop", "variables", "frame"]
    ref: int | None = Field(None, ge=1, description="variables: which variable to expand")
    frame_id: int | None = Field(None, ge=1, description="frame: which stack frame to show")


@router.post("/debug/{execution_id}/command", status_code=status.HTTP_202_ACCEPTED)
async def debug_command(
    project_id: int, execution_id: int, payload: DebugCommand, user: CurrentUser, db: DbSession
) -> dict:
    """Step the program (debugger role) or look at its variables (any member)."""
    if payload.command in control.STEP_COMMANDS:
        await _require_debugger(db, project_id, user.id)
    else:
        await get_member(db, project_id, user.id)
    try:
        active = await control.get_active(project_id)
        if not active or active.get("execution_id") != execution_id:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="That debug session has ended")
        delivered = await control.send_command(execution_id, {
            "command": payload.command, "ref": payload.ref, "frame_id": payload.frame_id,
            "user_id": user.id, "username": user.username,
        })
    except (RedisError, OSError) as e:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="The debugger is unavailable") from e
    return {"delivered": delivered > 0}
