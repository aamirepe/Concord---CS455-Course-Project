"""Initial schema

Revision ID: 0001_init
Revises:
Create Date: 2026-01-15 00:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0001_init"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("username", sa.String(64), nullable=False, unique=True),
        sa.Column("email", sa.String(255), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_users_username", "users", ["username"])
    op.create_index("ix_users_email", "users", ["email"])

    op.create_table(
        "projects",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("owner_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_projects_owner_id", "projects", ["owner_id"])

    session_status = sa.Enum("active", "closed", name="session_status")
    op.create_table(
        "sessions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("project_id", sa.Integer, sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_by", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("status", session_status, nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_sessions_project_id", "sessions", ["project_id"])

    session_role = sa.Enum("viewer", "editor", "debugger", "owner", name="session_role")
    op.create_table(
        "session_members",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("session_id", sa.Integer, sa.ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", session_role, nullable=False, server_default="viewer"),
        sa.Column("joined_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("session_id", "user_id", name="uq_session_user"),
    )
    op.create_index("ix_session_members_session_id", "session_members", ["session_id"])
    op.create_index("ix_session_members_user_id", "session_members", ["user_id"])

    op.create_table(
        "files",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("project_id", sa.Integer, sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("path", sa.String(512), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("project_id", "path", name="uq_project_path"),
    )
    op.create_index("ix_files_project_id", "files", ["project_id"])

    op.create_table(
        "document_versions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("file_id", sa.Integer, sa.ForeignKey("files.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version_number", sa.Integer, nullable=False),
        sa.Column("snapshot_bytes", sa.LargeBinary, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_document_versions_file_id", "document_versions", ["file_id"])

    op.create_table(
        "snapshots",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("session_id", sa.Integer, sa.ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("label", sa.String(255), nullable=False),
        sa.Column("created_by", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_snapshots_session_id", "snapshots", ["session_id"])

    op.create_table(
        "snapshot_files",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("snapshot_id", sa.Integer, sa.ForeignKey("snapshots.id", ondelete="CASCADE"), nullable=False),
        sa.Column("file_id", sa.Integer, sa.ForeignKey("files.id", ondelete="CASCADE"), nullable=False),
        sa.Column("content", sa.LargeBinary, nullable=False),
    )
    op.create_index("ix_snapshot_files_snapshot_id", "snapshot_files", ["snapshot_id"])

    execution_status = sa.Enum(
        "queued", "starting", "running", "completed", "failed", "timeout", "cancelled",
        name="execution_status",
    )
    op.create_table(
        "executions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("session_id", sa.Integer, sa.ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("file_id", sa.Integer, sa.ForeignKey("files.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", execution_status, nullable=False, server_default="queued"),
        sa.Column("exit_code", sa.Integer),
        sa.Column("stdout", sa.Text, nullable=False, server_default=""),
        sa.Column("stderr", sa.Text, nullable=False, server_default=""),
        sa.Column("queued_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("worker_id", sa.String(64)),
    )
    op.create_index("ix_executions_session_id", "executions", ["session_id"])
    op.create_index("ix_executions_status", "executions", ["status"])

    op.create_table(
        "execution_events",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("execution_id", sa.Integer, sa.ForeignKey("executions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("payload", sa.JSON, nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_execution_events_execution_id", "execution_events", ["execution_id"])
    op.create_index("ix_execution_events_event_type", "execution_events", ["event_type"])

    debug_status = sa.Enum("active", "paused", "stopped", name="debug_status")
    op.create_table(
        "debug_sessions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("execution_id", sa.Integer, sa.ForeignKey("executions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", debug_status, nullable=False, server_default="active"),
        sa.Column("current_line", sa.Integer),
        sa.Column("current_file", sa.String(512)),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_debug_sessions_execution_id", "debug_sessions", ["execution_id"])

    op.create_table(
        "breakpoints",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("file_id", sa.Integer, sa.ForeignKey("files.id", ondelete="CASCADE"), nullable=False),
        sa.Column("line", sa.Integer, nullable=False),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("created_by", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_breakpoints_file_id", "breakpoints", ["file_id"])


def downgrade() -> None:
    op.drop_table("breakpoints")
    op.drop_table("debug_sessions")
    op.execute("DROP TYPE IF EXISTS debug_status")
    op.drop_table("execution_events")
    op.drop_table("executions")
    op.execute("DROP TYPE IF EXISTS execution_status")
    op.drop_table("snapshot_files")
    op.drop_table("snapshots")
    op.drop_table("document_versions")
    op.drop_table("files")
    op.drop_table("session_members")
    op.execute("DROP TYPE IF EXISTS session_role")
    op.drop_table("sessions")
    op.execute("DROP TYPE IF EXISTS session_status")
    op.drop_table("projects")
    op.drop_table("users")
