"""The periodic CRDT checkpoint writes a version only when the document changed."""
from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.models import DocumentVersion
from app.realtime.checkpoint import checkpoint_once
from app.realtime.crdt import new_doc
from app.realtime.manager import room_manager


async def _versions(db, file_id: int) -> int:
    return (await db.execute(
        select(func.count()).select_from(DocumentVersion).where(DocumentVersion.file_id == file_id)
    )).scalar_one()


@pytest.mark.asyncio
async def test_unchanged_rooms_are_not_checkpointed_again(db, project):
    room = await room_manager.get_or_create_room(project["id"], project["file_id"])
    room.doc = new_doc("x = 1\n")

    await checkpoint_once()
    await checkpoint_once()  # nothing changed: no second row
    assert await _versions(db, project["file_id"]) == 1

    room.doc = new_doc("x = 2\n")
    await checkpoint_once()
    assert await _versions(db, project["file_id"]) == 2
