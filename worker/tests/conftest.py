"""Pytest fixtures for worker unit tests.

Mirrors backend/tests/conftest.py's SQLite bootstrap: worker/app/worker.py
imports app.db.session/app.models directly (PYTHONPATH=backend makes `app`
resolve to backend/app), so the same in-memory-SQLite override works here.
These tests run as their own pytest session (a separate CI job), so they need
their own copy of this setup rather than importing backend/tests/conftest.py.
"""
from __future__ import annotations

import os
import sys
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# The tests import `worker.app` from the repo root. `python -m pytest` puts the
# current directory on sys.path but plain `pytest` (as CI runs it) does not.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./worker_test.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("SECRET_KEY", "test-secret-key-32-bytes-min-len-for-tests")

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"

from app.core.config import get_settings  # noqa: E402

get_settings.cache_clear()  # type: ignore[attr-defined]
_settings = get_settings()
_settings.database_url = TEST_DB_URL

import app.db.session as session_module  # noqa: E402

_test_engine = create_async_engine(TEST_DB_URL, future=True)
session_module.engine = _test_engine
session_module.AsyncSessionLocal = async_sessionmaker(
    _test_engine, expire_on_commit=False, class_=AsyncSession
)

from app.models.base import Base  # noqa: E402


@pytest_asyncio.fixture(scope="function", autouse=True)
async def _setup_db() -> AsyncGenerator[None, None]:
    async with _test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with _test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture
async def db() -> AsyncGenerator[AsyncSession, None]:
    async with session_module.AsyncSessionLocal() as session:
        yield session


@pytest.fixture
def no_op_publish(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    """worker.py imports publish_project_event by name at module scope, so it
    must be patched there (not on app.realtime.execution_queue) to take effect."""
    import worker.app.worker as worker_module

    events: list[dict] = []

    async def _fake_publish(project_id: int, event: dict) -> None:
        events.append(event)

    monkeypatch.setattr(worker_module, "publish_project_event", _fake_publish)
    return events
