"""Debug session + breakpoint models."""
from __future__ import annotations

import enum
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.execution import Execution
    from app.models.file import File


class DebugStatus(enum.StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    STOPPED = "stopped"


class DebugSession(Base):
    __tablename__ = "debug_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    execution_id: Mapped[int | None] = mapped_column(
        ForeignKey("executions.id", ondelete="CASCADE"), index=True, nullable=True
    )
    status: Mapped[DebugStatus] = mapped_column(
        # Store the values ('active'), which is what the Postgres enum type has.
        Enum(DebugStatus, name="debug_status", values_callable=lambda e: [m.value for m in e]),
        default=DebugStatus.ACTIVE, nullable=False,
    )
    current_line: Mapped[int | None] = mapped_column(Integer, nullable=True)
    current_file: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # NULL on rows from before debug sessions were tied to projects.
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True, nullable=True
    )
    started_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    execution: Mapped[Execution | None] = relationship(back_populates="debug_session")


class Breakpoint(Base):
    __tablename__ = "breakpoints"

    id: Mapped[int] = mapped_column(primary_key=True)
    file_id: Mapped[int] = mapped_column(
        ForeignKey("files.id", ondelete="CASCADE"), index=True, nullable=False
    )
    line: Mapped[int] = mapped_column(Integer, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    file: Mapped[File] = relationship(back_populates="breakpoints")
