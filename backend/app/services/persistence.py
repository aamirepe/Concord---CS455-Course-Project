"""
Service for persisting and recovering Yjs CRDT state.
"""
from __future__ import annotations

import logging

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document_version import DocumentVersion

log = logging.getLogger(__name__)

class PersistenceManager:
    """
    Handles the binary persistence of Yjs documents to PostgreSQL.
    """

    @staticmethod
    async def save_checkpoint(session: AsyncSession, file_id: int, state_bytes: bytes) -> int:
        """
        Saves a binary snapshot of the document state.
        """
        try:
            # Get the current highest version number for this file
            stmt = (
                select(DocumentVersion.version_number)
                .where(DocumentVersion.file_id == file_id)
                .order_by(desc(DocumentVersion.version_number))
                .limit(1)
            )
            result = await session.execute(stmt)
            last_version = result.scalar() or 0

            # Create new version
            version = DocumentVersion(
                file_id=file_id,
                version_number=last_version + 1,
                snapshot_bytes=state_bytes
            )
            session.add(version)
            await session.flush()
            return version.id
        except Exception as e:
            log.error(f"Failed to save CRDT checkpoint for file {file_id}: {e}")
            raise

    @staticmethod
    async def load_latest_checkpoint(session: AsyncSession, file_id: int) -> bytes | None:
        """
        Retrieves the most recent binary snapshot for a file.
        """
        try:
            stmt = (
                select(DocumentVersion.snapshot_bytes)
                .where(DocumentVersion.file_id == file_id)
                .order_by(desc(DocumentVersion.version_number))
                .limit(1)
            )
            result = await session.execute(stmt)
            return result.scalar()
        except Exception as e:
            log.error(f"Failed to load CRDT checkpoint for file {file_id}: {e}")
            return None
