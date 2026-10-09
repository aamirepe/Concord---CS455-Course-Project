"""document_versions: index on (file_id, version_number)

Every checkpoint, Save and file open asks for a file's latest version; with
autosave pruning deleting old rows, the lookup should not scan the file's
whole history.

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
Create Date: 2026-09-28 10:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = 'c9d0e1f2a3b4'
down_revision: str | None = 'b8c9d0e1f2a3'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_document_versions_file_version", "document_versions", ["file_id", "version_number"]
    )


def downgrade() -> None:
    op.drop_index("ix_document_versions_file_version", table_name="document_versions")
