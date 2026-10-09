"""ai_tables_project_id

Revision 0002_ai_assistant created both AI tables keyed on ``session_id`` ->
``sessions.id``; the ORM models moved to ``project_id`` -> ``projects.id`` but no
migration followed, so on Postgres every assistant query failed. Under the
old schema no row could ever be written through the app, so the tables are
dropped and recreated rather than migrated in place.

Revision ID: d7a3c9e5f1b2
Revises: c4e1f7a2b9d0
Create Date: 2026-09-26 12:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ENUM as PG_ENUM

from alembic import op

revision: str = 'd7a3c9e5f1b2'
down_revision: str | None = 'c4e1f7a2b9d0'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_provider = PG_ENUM("mistral", "huggingface", name="aiprovider", create_type=False)


def upgrade() -> None:
    op.drop_table("ai_assistant_requests")
    op.drop_table("ai_assistant_configs")

    op.create_table(
        "ai_assistant_configs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("project_id", sa.Integer, sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("provider", _provider, nullable=False),
        sa.Column("encrypted_api_key", sa.Text, nullable=False),
        sa.Column("model_name", sa.String(100), nullable=True),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "ai_assistant_requests",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("project_id", sa.Integer, sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("file_id", sa.Integer, sa.ForeignKey("files.id", ondelete="SET NULL"), nullable=True),
        sa.Column("mode", sa.String(20), nullable=True),
        sa.Column("prompt", sa.Text, nullable=False),
        sa.Column("response", sa.Text, nullable=False),
        sa.Column("provider", _provider, nullable=False),
        sa.Column("model_used", sa.String(100), nullable=True),
        sa.Column("prompt_tokens", sa.Integer, nullable=True),
        sa.Column("completion_tokens", sa.Integer, nullable=True),
        sa.Column("total_tokens", sa.Integer, nullable=True),
        sa.Column("latency_ms", sa.Integer, nullable=True),
        sa.Column("is_successful", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_ai_assistant_requests_project_id", "ai_assistant_requests", ["project_id"])
    op.create_index("ix_ai_assistant_requests_created_at", "ai_assistant_requests", ["created_at"])


def downgrade() -> None:
    # Restore the (broken) session-keyed shape left by 0cb1cd51d0b5.
    op.drop_table("ai_assistant_requests")
    op.drop_table("ai_assistant_configs")

    op.create_table(
        "ai_assistant_configs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("session_id", sa.Integer, sa.ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("provider", _provider, nullable=False),
        sa.Column("encrypted_api_key", sa.Text, nullable=False),
        sa.Column("model_name", sa.String(100), nullable=True),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "ai_assistant_requests",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("session_id", sa.Integer, sa.ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("prompt", sa.Text, nullable=False),
        sa.Column("response", sa.Text, nullable=False),
        sa.Column("provider", _provider, nullable=False),
        sa.Column("model_used", sa.String(100), nullable=True),
        sa.Column("prompt_tokens", sa.Integer, nullable=True),
        sa.Column("completion_tokens", sa.Integer, nullable=True),
        sa.Column("total_tokens", sa.Integer, nullable=True),
        sa.Column("latency_ms", sa.Integer, nullable=True),
        sa.Column("is_successful", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
