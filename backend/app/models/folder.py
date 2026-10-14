"""Folder model for hierarchical project organization."""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.file import File
    from app.models.project import Project

class Folder(Base):
    __tablename__ = "folders"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True, nullable=False
    )
    parent_folder_id: Mapped[int | None] = mapped_column(
        ForeignKey("folders.id", ondelete="CASCADE"), index=True, nullable=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    project: Mapped[Project] = relationship(back_populates="folders")
    parent: Mapped[Folder | None] = relationship(
        "Folder", remote_side=[id], back_populates="subfolders"
    )
    subfolders: Mapped[list[Folder]] = relationship(
        "Folder", back_populates="parent", cascade="all, delete-orphan"
    )
    files: Mapped[list[File]] = relationship(back_populates="folder", cascade="all, delete-orphan")
