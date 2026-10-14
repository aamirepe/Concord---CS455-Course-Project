"""add_project_id_to_executions

Revision ID: 5d6e2ebf76bd
Revises: 6866bd04adf9
Create Date: 2026-09-11 07:40:01.062971
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = '5d6e2ebf76bd'
down_revision: str | None = '6866bd04adf9'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("executions", sa.Column("project_id", sa.Integer, sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=True))
    op.create_index("ix_executions_project_id", "executions", ["project_id"])



def downgrade() -> None:
    op.drop_index("ix_executions_project_id")
    op.drop_column("executions", "project_id")

