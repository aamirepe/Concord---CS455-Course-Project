"""make_session_id_nullable_in_executions

Revision ID: 9eb766bc1889
Revises: 5d6e2ebf76bd
Create Date: 2026-09-11 08:56:55.920417
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = '9eb766bc1889'
down_revision: str | None = '5d6e2ebf76bd'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("executions", "session_id", nullable=True)



def downgrade() -> None:
    pass
