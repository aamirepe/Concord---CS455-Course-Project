"""execution_status_compiling

The ORM's ``ExecutionStatus`` gained ``compiling`` (used by the worker for
compiled languages and by the orphan sweep), but no migration added it to the
Postgres ``execution_status`` type, so on Postgres every write or query with
that value failed.

Revision ID: e8b4d0f6a2c3
Revises: d7a3c9e5f1b2
Create Date: 2026-09-26 13:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = 'e8b4d0f6a2c3'
down_revision: str | None = 'd7a3c9e5f1b2'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TYPE execution_status ADD VALUE IF NOT EXISTS 'compiling' AFTER 'starting'")


def downgrade() -> None:
    # Postgres can't drop a value from an enum type; leaving it is harmless.
    pass
