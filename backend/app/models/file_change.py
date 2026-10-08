"""Who changed a file and by how many lines.

One row per author per attribution window (a burst of edits, closed after a
few idle seconds or at most a minute, or by an explicit save). Written by the
checkpointer next to the ``document_versions`` it creates; the project's
"recent changes" panel sums them per file and author.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class FileChange(Base):
    __tablename__ = "file_changes"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    file_id: Mapped[int] = mapped_column(ForeignKey("files.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    lines_added: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lines_removed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
