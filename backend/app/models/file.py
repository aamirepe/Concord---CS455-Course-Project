"""File model — one Project has many Files. The CRDT lives in document_versions."""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.breakpoint import Breakpoint
    from app.models.document_version import DocumentVersion
    from app.models.execution import Execution
    from app.models.folder import Folder
    from app.models.project import Project

class File(Base):
    __tablename__ = "files"
    __table_args__ = (UniqueConstraint("project_id", "path", name="uq_project_path"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True, nullable=False
    )
    folder_id: Mapped[int | None] = mapped_column(
        ForeignKey("folders.id", ondelete="SET NULL"), index=True, nullable=True
    )
    path: Mapped[str] = mapped_column(String(512), nullable=False)
    language: Mapped[str] = mapped_column(String(50), nullable=False, default="python")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    project: Mapped[Project] = relationship(back_populates="files")
    folder: Mapped[Folder | None] = relationship(back_populates="files")
    versions: Mapped[list[DocumentVersion]] = relationship(back_populates="file")
    executions: Mapped[list[Execution]] = relationship(back_populates="file")
    breakpoints: Mapped[list[Breakpoint]] = relationship(back_populates="file")
