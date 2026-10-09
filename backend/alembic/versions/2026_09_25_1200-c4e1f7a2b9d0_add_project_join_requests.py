"""add_project_join_requests

Revision ID: c4e1f7a2b9d0
Revises: 9eb766bc1889
Create Date: 2026-09-25 12:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = 'c4e1f7a2b9d0'
down_revision: str | None = '9eb766bc1889'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "project_join_requests",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("project_id", sa.Integer, sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_project_join_requests_project_id", "project_join_requests", ["project_id"])
    op.create_index("ix_project_join_requests_user_id", "project_join_requests", ["user_id"])


def downgrade() -> None:
    op.drop_table("project_join_requests")
