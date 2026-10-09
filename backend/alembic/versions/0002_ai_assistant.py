"""Add AI assistant tables

Revision ID: 0002_ai_assistant
Revises: 0001_init
Create Date: 2026-08-31 00:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002_ai_assistant"
down_revision: str | None = "0001_init"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    print("Dropping enum type if exists...")
    op.execute("DROP TYPE IF EXISTS aiprovider CASCADE")
    print("Creating enum type...")
    op.execute("CREATE TYPE aiprovider AS ENUM ('mistral', 'huggingface')")
    print("Enum type created.")

    # Import ENUM from postgresql dialect for use with create_type=False
    from sqlalchemy.dialects.postgresql import ENUM as PG_ENUM

    # Create AI assistant configs table
    print("Creating ai_assistant_configs table...")
    op.create_table(
        "ai_assistant_configs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("session_id", sa.Integer, sa.ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("provider", PG_ENUM("mistral", "huggingface", name="aiprovider", create_type=False), nullable=False),
        sa.Column("encrypted_api_key", sa.Text, nullable=False),
        sa.Column("model_name", sa.String(100), nullable=True),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now(), nullable=False),
    )
    op.create_index("ix_ai_assistant_configs_session_id", "ai_assistant_configs", ["session_id"])
    print("ai_assistant_configs table created.")

    # Create AI assistant requests table
    print("Creating ai_assistant_requests table...")
    op.create_table(
        "ai_assistant_requests",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("session_id", sa.Integer, sa.ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("prompt", sa.Text, nullable=False),
        sa.Column("response", sa.Text, nullable=False),
        sa.Column("provider", PG_ENUM("mistral", "huggingface", name="aiprovider", create_type=False), nullable=False),
        sa.Column("model_used", sa.String(100), nullable=True),
        sa.Column("completion_tokens", sa.Integer, nullable=True),
        sa.Column("total_tokens", sa.Integer, nullable=True),
        sa.Column("latency_ms", sa.Integer, nullable=True),
        sa.Column("is_successful", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_ai_assistant_requests_session_id", "ai_assistant_requests", ["session_id"])
    op.create_index("ix_ai_assistant_requests_user_id", "ai_assistant_requests", ["user_id"])
    op.create_index("ix_ai_assistant_requests_created_at", "ai_assistant_requests", ["created_at"])
    print("ai_assistant_requests table created.")


def downgrade() -> None:
    # Drop tables in reverse order
    op.drop_index("ix_ai_assistant_requests_created_at", table_name="ai_assistant_requests")
    op.drop_index("ix_ai_assistant_requests_user_id", table_name="ai_assistant_requests")
    op.drop_index("ix_ai_assistant_requests_session_id", table_name="ai_assistant_requests")
    op.drop_table("ai_assistant_requests")

    op.drop_index("ix_ai_assistant_configs_session_id", table_name="ai_assistant_configs")
    op.drop_table("ai_assistant_configs")

    # Drop enum type
    op.execute("DROP TYPE aiprovider")
