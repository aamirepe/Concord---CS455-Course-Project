"""Add language column to files table

Revision ID: 0003_add_file_language
Revises: 0002_ai_assistant
Create Date: 2026-09-02 00:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003_add_file_language"
down_revision: str | None = "0002_ai_assistant"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("files", sa.Column("language", sa.String(length=50), nullable=False, server_default="python"))


def downgrade() -> None:
    op.drop_column("files", "language")
