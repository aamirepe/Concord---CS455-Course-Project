"""add_project_members_table

Revision ID: 6866bd04adf9
Revises: 0cb1cd51d0b5
Create Date: 2026-09-11 07:10:43.224404
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = '6866bd04adf9'
down_revision: str | None = '0cb1cd51d0b5'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    project_role = sa.Enum("viewer", "editor", "debugger", "owner", name="project_role")
    op.create_table(
        "project_members",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("project_id", sa.Integer, sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", project_role, nullable=False, server_default="viewer"),
        sa.Column("joined_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_project_members_project_id", "project_members", ["project_id"])
    op.create_index("ix_project_members_user_id", "project_members", ["user_id"])
    op.create_unique_constraint("uq_project_user", "project_members", ["project_id", "user_id"])



def downgrade() -> None:
    op.drop_table("project_members")
    op.execute("DROP TYPE IF EXISTS project_role")

