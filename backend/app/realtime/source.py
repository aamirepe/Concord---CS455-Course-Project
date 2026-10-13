"""Read the current text of a file.

A file's authoritative content is the live CRDT doc when someone has it open;
otherwise it is the latest checkpoint in ``document_versions``. Execution and
the AI agent both need "what the code looks like right now", so the lookup
lives here rather than in either caller.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import DocumentVersion
from app.realtime.crdt import doc_from_bytes, doc_to_text
from app.realtime.manager import room_manager


async def get_file_text(db: AsyncSession, project_id: int, file_id: int) -> str:
    room = room_manager.get_room(project_id, file_id)
    if room:
        return doc_to_text(room.doc)
    res = await db.execute(
        select(DocumentVersion)
        .where(DocumentVersion.file_id == file_id)
        .order_by(DocumentVersion.version_number.desc())
        .limit(1)
    )
    latest = res.scalar_one_or_none()
    if latest is None:
        return ""
    return doc_to_text(doc_from_bytes(latest.snapshot_bytes))
