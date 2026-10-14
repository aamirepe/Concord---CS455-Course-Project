"""file_changes: per-author line counts for the recent-changes panel

Revision ID: f1a2b3c4d5e6
Revises: e8b4d0f6a2c3
Create Date: 2026-09-27 12:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = 'f1a2b3c4d5e6'
down_revision: str | None = 'e8b4d0f6a2c3'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "file_changes",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("project_id", sa.Integer, sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("file_id", sa.Integer, sa.ForeignKey("files.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("lines_added", sa.Integer, nullable=False, server_default="0"),
        sa.Column("lines_removed", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_file_changes_project_id", "file_changes", ["project_id"])
    op.create_index("ix_file_changes_file_id", "file_changes", ["file_id"])
    op.create_index("ix_file_changes_user_id", "file_changes", ["user_id"])


def downgrade() -> None:
    op.drop_table("file_changes")
