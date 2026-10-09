"""snapshots: session_id -> project_id

Sessions were folded into projects, and the Snapshot model moved to
``project_id``, but no migration changed the table: on Postgres every snapshot
request failed with 'column "project_id" of relation "snapshots" does not
exist' (the SQLite tests build the schema from the models, so they passed).

Existing rows are moved to their session's project.

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-09-27 14:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = 'b8c9d0e1f2a3'
down_revision: str | None = 'a7b8c9d0e1f2'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("snapshots", sa.Column("project_id", sa.Integer, nullable=True))
    op.execute(
        "UPDATE snapshots SET project_id = sessions.project_id FROM sessions WHERE sessions.id = snapshots.session_id"
    )
    op.execute("DELETE FROM snapshots WHERE project_id IS NULL")
    op.alter_column("snapshots", "project_id", nullable=False)
    op.create_foreign_key(
        "snapshots_project_id_fkey", "snapshots", "projects", ["project_id"], ["id"], ondelete="CASCADE"
    )
    op.create_index("ix_snapshots_project_id", "snapshots", ["project_id"])
    op.drop_index("ix_snapshots_session_id", table_name="snapshots")
    op.drop_constraint("snapshots_session_id_fkey", "snapshots", type_="foreignkey")
    op.drop_column("snapshots", "session_id")


def downgrade() -> None:
    # Snapshots made after the upgrade have no session to go back to.
    op.execute("DELETE FROM snapshots")
    op.add_column("snapshots", sa.Column("session_id", sa.Integer, nullable=False))
    op.create_foreign_key(
        "snapshots_session_id_fkey", "snapshots", "sessions", ["session_id"], ["id"], ondelete="CASCADE"
    )
    op.create_index("ix_snapshots_session_id", "snapshots", ["session_id"])
    op.drop_index("ix_snapshots_project_id", table_name="snapshots")
    op.drop_constraint("snapshots_project_id_fkey", "snapshots", type_="foreignkey")
    op.drop_column("snapshots", "project_id")
