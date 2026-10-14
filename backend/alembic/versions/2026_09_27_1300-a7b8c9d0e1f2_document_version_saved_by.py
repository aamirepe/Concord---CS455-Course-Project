"""document_versions.saved_by: who saved (or restored) a version; NULL for autosaves

Revision ID: a7b8c9d0e1f2
Revises: f1a2b3c4d5e6
Create Date: 2026-09-27 13:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = 'a7b8c9d0e1f2'
down_revision: str | None = 'f1a2b3c4d5e6'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("document_versions") as batch:
        batch.add_column(sa.Column("saved_by", sa.Integer, nullable=True))
        batch.create_foreign_key(
            "fk_document_versions_saved_by_users", "users", ["saved_by"], ["id"], ondelete="SET NULL"
        )


def downgrade() -> None:
    with op.batch_alter_table("document_versions") as batch:
        batch.drop_constraint("fk_document_versions_saved_by_users", type_="foreignkey")
        batch.drop_column("saved_by")
