"""Opening a file's live room, and replacing its text as a normal edit.

A restore (of a version or a snapshot) must reach people who have the file
open. Swapping the room's doc for an older one doesn't: their editors already
hold every item in the older state, so merging it changes nothing and the
server silently diverges from them. Instead, ``replace_text`` makes the edit
"change the text to X" as a CRDT update on the current doc, which syncs like
typing, is attributed to whoever restored, and survives reconnects.
"""
from __future__ import annotations

from pycrdt import Text
from sqlalchemy import select

from app.db.session import AsyncSessionLocal
from app.models import DocumentVersion
from app.realtime.crdt import TEXT_TYPE, doc_from_bytes, doc_to_bytes, doc_to_text
from app.realtime.manager import Room, room_manager


async def open_room(project_id: int, file_id: int) -> Room:
    """The file's live room, loaded from its latest checkpoint the first time."""
    room = await room_manager.get_or_create_room(project_id, file_id)
    async with room.lock:
        if not room.hydrated:
            async with AsyncSessionLocal() as db:
                latest = (await db.execute(
                    select(DocumentVersion)
                    .where(DocumentVersion.file_id == file_id)
                    .order_by(DocumentVersion.version_number.desc())
                    .limit(1)
                )).scalar_one_or_none()
            if latest:
                room.doc = doc_from_bytes(latest.snapshot_bytes)
            room.hydrated = True
    return room


def text_edit_update(room: Room, new_text: str) -> bytes | None:
    """A CRDT update turning the room's text into ``new_text`` with one minimal edit.

    Only the differing middle is replaced (common prefix and suffix kept), so
    other people's cursors outside it stay put. ``None`` if nothing changes.
    """
    old = doc_to_text(room.doc)
    if old == new_text:
        return None
    start = 0
    limit = min(len(old), len(new_text))
    while start < limit and old[start] == new_text[start]:
        start += 1
    end_old, end_new = len(old), len(new_text)
    while end_old > start and end_new > start and old[end_old - 1] == new_text[end_new - 1]:
        end_old -= 1
        end_new -= 1

    scratch = doc_from_bytes(doc_to_bytes(room.doc))
    before = scratch.get_state()
    text = scratch.get(TEXT_TYPE, type=Text)
    with scratch.transaction():
        if end_old > start:
            del text[start:end_old]
        if end_new > start:
            text.insert(start, new_text[start:end_new])
    return bytes(scratch.get_update(before))


async def replace_text(room: Room, new_text: str, user_id: int) -> bool:
    """Make ``new_text`` the room's content, as ``user_id``'s edit, and send it to the open editors."""
    async with room.lock:
        update = text_edit_update(room, new_text)
        if update is None:
            return False
        room.apply_edit(user_id, update)
    room_manager.broadcast(room, update)
    return True
