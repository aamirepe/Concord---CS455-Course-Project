"""Periodic CRDT checkpointing, explicit saves, and change attribution.

Writes a `document_versions` row for each changed room every few seconds. This
is the bridge between the live Yjs state (in memory) and the durable store
(Postgres) that snapshot restore and version history read from.

It does NOT run on every keystroke. That would be orders of magnitude too
much load on the DB and is unnecessary — Yjs guarantees all clients converge
without our help; checkpoints are only needed for durability and reconnect
after server restart.

Alongside, it closes each room's attribution window into `file_changes` rows
(who changed the file, lines +/-) once the file has been idle for a
checkpoint interval, or the window is a minute old, or someone presses Save,
and tells the project socket with a ``FILE_CHANGED`` event.

Every ten minutes it also thins out old autosaves (see ``versions_to_prune``),
so a file edited all day keeps a usable history rather than thousands of rows.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import AsyncSessionLocal
from app.models import DocumentVersion, FileChange, User
from app.realtime.crdt import doc_to_bytes
from app.realtime.manager import Room, room_manager

log = logging.getLogger(__name__)

CHECKPOINT_INTERVAL_SECONDS = 5
# An attribution window closes after this long even if edits keep coming.
MAX_WINDOW_SECONDS = 60

# How often old autosaves are thinned out, and the history they keep:
# everything from the last hour, one per 10 minutes for a day, then one a day.
PRUNE_INTERVAL_SECONDS = 600
KEEP_ALL_FOR = timedelta(hours=1)
KEEP_TEN_MINUTELY_FOR = timedelta(days=1)
# Files with fewer versions than this aren't worth looking at.
PRUNE_MIN_VERSIONS = 30

# The loop and Save both number versions; one at a time.
_lock = asyncio.Lock()


@dataclass
class CheckpointResult:
    version: int | None  # latest version number after this call (None: file never saved)
    created: bool  # whether a new version row was written
    saved_at: datetime | None = None
    changes: list[dict] = field(default_factory=list)  # the FILE_CHANGED entries


async def checkpoint_loop() -> None:
    last_prune = time.monotonic()
    while True:
        try:
            await asyncio.sleep(CHECKPOINT_INTERVAL_SECONDS)
            await checkpoint_once()
            if time.monotonic() - last_prune >= PRUNE_INTERVAL_SECONDS:
                last_prune = time.monotonic()
                await prune_versions()
        except asyncio.CancelledError:
            return
        except Exception:
            log.exception("checkpoint iteration failed")


async def checkpoint_once(force: bool = False) -> None:
    """Checkpoint every live room. ``force`` also closes open attribution windows (shutdown)."""
    if not room_manager._rooms:
        return
    async with _lock, AsyncSessionLocal() as db:
        results = []
        for room in list(room_manager._rooms.values()):
            results.append((room, await _checkpoint_room(db, room, close_window=force or _window_due(room))))
        await db.commit()
    for room, result in results:
        _announce(room, result)


async def save_room(room: Room, user_id: int | None = None) -> CheckpointResult:
    """Explicit save: write a version now (recording who saved it) and close the attribution window."""
    async with _lock, AsyncSessionLocal() as db:
        result = await _checkpoint_room(db, room, close_window=True, saved_by=user_id)
        await db.commit()
    _announce(room, result)
    return result


def _window_due(room: Room) -> bool:
    if not room.window_authors:
        return bool(room.pending)
    now = time.time()
    return now - room.last_edit >= CHECKPOINT_INTERVAL_SECONDS or now - room.window_started >= MAX_WINDOW_SECONDS


async def _checkpoint_room(
    db: AsyncSession, room: Room, *, close_window: bool, saved_by: int | None = None
) -> CheckpointResult:
    payload = doc_to_bytes(room.doc)
    res = await db.execute(
        select(DocumentVersion)
        .where(DocumentVersion.file_id == room.file_id)
        .order_by(DocumentVersion.version_number.desc())
        .limit(1)
    )
    latest = res.scalar_one_or_none()
    result = CheckpointResult(
        version=latest.version_number if latest else None,
        created=False,
        saved_at=latest.created_at if latest else None,
    )
    if payload and (latest is None or latest.snapshot_bytes != payload):
        result.version = (latest.version_number + 1) if latest else 1
        result.saved_at = datetime.now(UTC)
        result.created = True
        db.add(DocumentVersion(
            file_id=room.file_id, version_number=result.version, snapshot_bytes=payload,
            created_at=result.saved_at, saved_by=saved_by,
        ))
    elif saved_by is not None and latest is not None and latest.saved_by is None:
        # Saving right after an autosave of the same text: that version is now a save.
        latest.saved_by = saved_by

    if close_window:
        room.close_window()
        shares = room.take_pending()
        if shares:
            names = dict((await db.execute(select(User.id, User.username).where(User.id.in_(shares)))).tuples().all())
            for uid, (a, r) in shares.items():
                db.add(FileChange(project_id=room.project_id, file_id=room.file_id, user_id=uid, lines_added=a, lines_removed=r))
                result.changes.append({"user_id": uid, "username": names.get(uid, f"user-{uid}"), "lines_added": a, "lines_removed": r})
    return result


def _announce(room: Room, result: CheckpointResult) -> None:
    if not result.changes:
        return
    room_manager.broadcast_to_project(room.project_id, json.dumps({
        "op": "FILE_CHANGED",
        "file_id": room.file_id,
        "version": result.version,
        "changes": result.changes,
    }))


# --- pruning -------------------------------------------------------------------

@dataclass(frozen=True)
class VersionRow:
    id: int
    version_number: int
    created_at: datetime
    saved_by: int | None


def versions_to_prune(rows: Iterable[VersionRow], now: datetime) -> list[int]:
    """Ids of one file's autosaves that the history no longer needs.

    Always kept: the latest version, every Save (``saved_by`` set) and every
    version from the last hour. Older autosaves keep the newest one per
    10-minute slot for a day, then the newest per day.
    """
    rows = sorted(rows, key=lambda r: r.version_number, reverse=True)
    if not rows:
        return []
    now = _aware(now)
    seen_slots: set[tuple[str, int]] = set()
    prune: list[int] = []
    for i, row in enumerate(rows):
        age = now - _aware(row.created_at)
        if age < KEEP_ALL_FOR:
            continue
        ts = int(_aware(row.created_at).timestamp())
        slot = ("10m", ts // 600) if age < KEEP_TEN_MINUTELY_FOR else ("day", ts // 86400)
        if i == 0 or row.saved_by is not None:
            seen_slots.add(slot)  # kept regardless, and it stands for its slot
        elif slot in seen_slots:
            prune.append(row.id)  # an older autosave in a slot that already has a newer one
        else:
            seen_slots.add(slot)
    return prune


def _aware(dt: datetime) -> datetime:
    # SQLite hands back naive datetimes; they are UTC.
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


async def prune_versions(now: datetime | None = None) -> int:
    """Delete autosaves the history doesn't need, across all files. Returns how many."""
    now = now or datetime.now(UTC)
    deleted = 0
    async with AsyncSessionLocal() as db:
        busy = (await db.execute(
            select(DocumentVersion.file_id)
            .group_by(DocumentVersion.file_id)
            .having(func.count() >= PRUNE_MIN_VERSIONS)
        )).scalars().all()
        for file_id in busy:
            rows = (await db.execute(
                select(DocumentVersion.id, DocumentVersion.version_number,
                       DocumentVersion.created_at, DocumentVersion.saved_by)
                .where(DocumentVersion.file_id == file_id)
            )).all()
            ids = versions_to_prune((VersionRow(*r) for r in rows), now)
            for i in range(0, len(ids), 500):  # bounded IN lists
                await db.execute(delete(DocumentVersion).where(DocumentVersion.id.in_(ids[i:i + 500])))
            deleted += len(ids)
        await db.commit()
    if deleted:
        log.info("pruned %d old autosaves", deleted)
    return deleted
