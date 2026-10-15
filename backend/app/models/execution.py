"""Execution + state machine."""
from __future__ import annotations

import enum
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    JSON,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models import Project
    from app.models.debug_session import DebugSession
    from app.models.file import File


class ExecutionStatus(enum.StrEnum):
    QUEUED = "queued"
    STARTING = "starting"
    COMPILING = "compiling"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


# State machine — explicit transition table.
ALLOWED_TRANSITIONS: dict[ExecutionStatus, set[ExecutionStatus]] = {
    ExecutionStatus.QUEUED: {ExecutionStatus.STARTING, ExecutionStatus.COMPILING, ExecutionStatus.CANCELLED, ExecutionStatus.FAILED},
    ExecutionStatus.STARTING: {ExecutionStatus.RUNNING, ExecutionStatus.COMPILING, ExecutionStatus.FAILED, ExecutionStatus.TIMEOUT},
    ExecutionStatus.COMPILING: {
        ExecutionStatus.RUNNING,
        ExecutionStatus.FAILED,
        ExecutionStatus.TIMEOUT,
        ExecutionStatus.CANCELLED,
    },
    ExecutionStatus.RUNNING: {
        ExecutionStatus.COMPLETED,
        ExecutionStatus.FAILED,
        ExecutionStatus.TIMEOUT,
        ExecutionStatus.CANCELLED,
    },
    ExecutionStatus.COMPLETED: set(),
    ExecutionStatus.FAILED: set(),
    ExecutionStatus.TIMEOUT: set(),
    ExecutionStatus.CANCELLED: set(),
}


def can_transition(current: ExecutionStatus, target: ExecutionStatus) -> bool:
    return target in ALLOWED_TRANSITIONS.get(current, set())


class Execution(Base):
    __tablename__ = "executions"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True, nullable=False
    )
    session_id: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)
    file_id: Mapped[int] = mapped_column(ForeignKey("files.id", ondelete="CASCADE"), nullable=False)
    status: Mapped[ExecutionStatus] = mapped_column(
        Enum("queued", "starting", "compiling", "running", "completed", "failed", "timeout", "cancelled", name="execution_status"),
        default=ExecutionStatus.QUEUED,
        index=True,
        nullable=False,
    )
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    stdout: Mapped[str] = mapped_column(Text, default="", nullable=False)
    stderr: Mapped[str] = mapped_column(Text, default="", nullable=False)
    queued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    worker_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    file: Mapped[File] = relationship(back_populates="executions")
    project: Mapped[Project] = relationship()
    events: Mapped[list[ExecutionEvent]] = relationship(
        back_populates="execution", cascade="all, delete-orphan"
    )
    debug_session: Mapped[DebugSession | None] = relationship(
        back_populates="execution", uselist=False
    )


class ExecutionEvent(Base):
    """Append-only audit trail used by the metrics dashboard."""
    __tablename__ = "execution_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    execution_id: Mapped[int] = mapped_column(
        ForeignKey("executions.id", ondelete="CASCADE"), index=True, nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    execution: Mapped[Execution] = relationship(back_populates="events")
