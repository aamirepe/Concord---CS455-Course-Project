"""The model enums must match what the migrations create on Postgres.

Tests run on SQLite, where enum columns are plain strings, so a value added to
a model but not to a migration only fails in production. This reads the
migration files and checks every model value is created somewhere.
"""
from __future__ import annotations

import re
from pathlib import Path

from app.models import ExecutionStatus

VERSIONS = Path(__file__).resolve().parents[1] / "alembic" / "versions"


def test_every_execution_status_exists_in_migrations():
    text = "\n".join(p.read_text() for p in VERSIONS.glob("*.py"))
    created = set(re.findall(r'"(\w+)"', re.search(r'execution_status = sa\.Enum\((.*?)name=', text, re.S).group(1)))
    added = set(re.findall(r"ALTER TYPE execution_status ADD VALUE IF NOT EXISTS '(\w+)'", text))
    assert {s.value for s in ExecutionStatus} <= created | added


def test_enum_columns_store_values_like_the_migrations():
    """SQLAlchemy stores an enum's *names* ('ACTIVE') unless told otherwise, but
    the migrations create Postgres types with the *values* ('active'); a
    mismatch fails every insert on Postgres (it did for aiprovider and
    debug_status) while SQLite accepts anything."""
    from sqlalchemy import Enum

    from app.models.base import Base

    wrong = []
    for table in Base.metadata.tables.values():
        for col in table.columns:
            if isinstance(col.type, Enum) and col.type.enum_class is not None:
                values = [m.value for m in col.type.enum_class]
                if list(col.type.enums) != values:
                    wrong.append(f"{table.name}.{col.name}: {list(col.type.enums)} != {values}")
    assert wrong == []
