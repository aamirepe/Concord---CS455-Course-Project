"""Periodic CRDT checkpoints. NOT per-keystroke."""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, Integer, LargeBinary, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.file import File


class DocumentVersion(Base):
    __tablename__ = "document_versions"
    # "Latest version of file X" runs on every checkpoint, Save and open.
    __table_args__ = (Index("ix_document_versions_file_version", "file_id", "version_number"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    file_id: Mapped[int] = mapped_column(
        ForeignKey("files.id", ondelete="CASCADE"), index=True, nullable=False
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    # CRDT state encoded with pycrdt.Doc.encode_state_as_update(doc)
    snapshot_bytes: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # Who pressed Save (or restored) for this version; NULL for autosaves.
    saved_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    file: Mapped[File] = relationship(back_populates="versions")
