"""debug_sessions: project, who started it, when it ended

Debug sessions used to be matched to runs by file path alone (in any
project); now each is created with its run and belongs to a project.

Revision ID: d0e1f2a3b4c5
Revises: c9d0e1f2a3b4
Create Date: 2026-09-28 11:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = 'd0e1f2a3b4c5'
down_revision: str | None = 'c9d0e1f2a3b4'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("debug_sessions") as batch:
        batch.add_column(sa.Column("project_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("started_by", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True))
        batch.create_foreign_key("fk_debug_sessions_project_id", "projects", ["project_id"], ["id"], ondelete="CASCADE")
        batch.create_foreign_key("fk_debug_sessions_started_by", "users", ["started_by"], ["id"], ondelete="SET NULL")
        batch.create_index("ix_debug_sessions_project_id", ["project_id"])
    # Sessions linked to a run get that run's project.
    op.execute(
        "UPDATE debug_sessions SET project_id = "
        "(SELECT executions.project_id FROM executions WHERE executions.id = debug_sessions.execution_id) "
        "WHERE execution_id IS NOT NULL"
    )


def downgrade() -> None:
    with op.batch_alter_table("debug_sessions") as batch:
        batch.drop_index("ix_debug_sessions_project_id")
        batch.drop_constraint("fk_debug_sessions_started_by", type_="foreignkey")
        batch.drop_constraint("fk_debug_sessions_project_id", type_="foreignkey")
        batch.drop_column("ended_at")
        batch.drop_column("started_by")
        batch.drop_column("project_id")
