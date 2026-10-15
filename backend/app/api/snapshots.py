"""Snapshots: a labeled copy of every file in the project; list; restore.

Restoring replaces each file's text as a normal edit by the restorer (see
``app.realtime.rooms``), so people with a file open see it change, and it
first takes a safety snapshot of the current state so a restore can be undone.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.deps import CurrentUser, DbSession, get_member
from app.models import (
    DocumentVersion,
    File,
    ProjectMember,
    Role,
    Snapshot,
    SnapshotFile,
    User,
)
from app.realtime.checkpoint import save_room
from app.realtime.crdt import doc_from_bytes, doc_to_bytes, doc_to_text, new_doc
from app.realtime.manager import room_manager
from app.realtime.rooms import open_room, replace_text
from app.schemas import SnapshotCreate, SnapshotOut, SnapshotRestoreOut

router = APIRouter(prefix="/projects/{project_id}/snapshots", tags=["snapshots"])


async def _require_owner_or_editor(db: AsyncSession, project_id: int, user_id: int) -> ProjectMember:
    member = await get_member(db, project_id, user_id)
    if Role(member.role.value if hasattr(member.role, 'value') else member.role) not in {Role.OWNER, Role.EDITOR, Role.DEBUGGER}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Requires editor+ role"
        )
    return member


async def _take_snapshot(db: AsyncSession, project_id: int, label: str, user_id: int) -> Snapshot:
    """Store every file's current content: the live doc if it's open, else its latest version."""
    snapshot = Snapshot(project_id=project_id, label=label, created_by=user_id)
    db.add(snapshot)
    await db.flush()

    files = (await db.execute(select(File.id).where(File.project_id == project_id))).scalars().all()
    for file_id in files:
        room = room_manager.get_room(project_id, file_id)
        content: bytes | None
        if room is not None and room.hydrated:
            content = doc_to_bytes(room.doc)
        else:
            content = (await db.execute(
                select(DocumentVersion.snapshot_bytes)
                .where(DocumentVersion.file_id == file_id)
                .order_by(DocumentVersion.version_number.desc())
                .limit(1)
            )).scalar_one_or_none()
        if content is None:  # never opened: its saved state is the empty document
            content = doc_to_bytes(new_doc())
        db.add(SnapshotFile(snapshot_id=snapshot.id, file_id=file_id, content=content))
    return snapshot


async def _out(db: AsyncSession, snapshot_ids: list[int]) -> list[SnapshotOut]:
    counts = dict((await db.execute(
        select(SnapshotFile.snapshot_id, func.count())
        .where(SnapshotFile.snapshot_id.in_(snapshot_ids))
        .group_by(SnapshotFile.snapshot_id)
    )).tuples().all())
    rows = (await db.execute(
        select(Snapshot, User.username)
        .outerjoin(User, User.id == Snapshot.created_by)
        .where(Snapshot.id.in_(snapshot_ids))
        .order_by(Snapshot.id.desc())
    )).tuples().all()
    return [
        SnapshotOut(
            id=s.id, project_id=s.project_id, label=s.label, created_at=s.created_at,
            created_by_username=username, file_count=counts.get(s.id, 0),
        )
        for s, username in rows
    ]


@router.post("", response_model=SnapshotOut, status_code=status.HTTP_201_CREATED)
async def create_snapshot(
    project_id: int, payload: SnapshotCreate, user: CurrentUser, db: DbSession
) -> SnapshotOut:
    await _require_owner_or_editor(db, project_id, user.id)
    snapshot = await _take_snapshot(db, project_id, payload.label, user.id)
    await db.commit()
    return (await _out(db, [snapshot.id]))[0]


@router.get("", response_model=list[SnapshotOut])
async def list_snapshots(
    project_id: int, user: CurrentUser, db: DbSession
) -> list[SnapshotOut]:
    await get_member(db, project_id, user.id)
    ids = (await db.execute(select(Snapshot.id).where(Snapshot.project_id == project_id))).scalars().all()
    return await _out(db, list(ids))


@router.post("/{snapshot_id}/restore", response_model=SnapshotRestoreOut)
async def restore_snapshot(
    project_id: int, snapshot_id: int, user: CurrentUser, db: DbSession
) -> SnapshotRestoreOut:
    await _require_owner_or_editor(db, project_id, user.id)

    snapshot = await db.get(Snapshot, snapshot_id, options=[selectinload(Snapshot.files)])
    if not snapshot or snapshot.project_id != project_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Snapshot not found")

    safety = await _take_snapshot(db, project_id, f"Before restoring '{snapshot.label}'"[:255], user.id)
    await db.commit()

    restored = 0
    for sf in snapshot.files:
        room = await open_room(project_id, sf.file_id)
        if await replace_text(room, doc_to_text(doc_from_bytes(sf.content)), user.id):
            await save_room(room, user.id)
            restored += 1

    return SnapshotRestoreOut(snapshot_id=snapshot_id, restored_files=restored, safety_snapshot_id=safety.id)
