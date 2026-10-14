"""Project model."""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.ai_assistant import AIAssistantConfig
    from app.models.file import File
    from app.models.folder import Folder
    from app.models.invitation import Invitation
    from app.models.project_member import ProjectMember
    from app.models.user import User

class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    owner: Mapped[User] = relationship(back_populates="projects")
    members: Mapped[list[ProjectMember]] = relationship(back_populates="project", cascade="all, delete-orphan")
    files: Mapped[list[File]] = relationship(back_populates="project")
    folders: Mapped[list[Folder]] = relationship(back_populates="project", cascade="all, delete-orphan")
    invitations: Mapped[list[Invitation]] = relationship(back_populates="project", cascade="all, delete-orphan")
    ai_config: Mapped[AIAssistantConfig] = relationship(back_populates="project", uselist=False)
