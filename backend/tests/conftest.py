"""Pytest fixtures for backend tests."""
from __future__ import annotations

import os
import uuid
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Ensure tests use the in-process SQLite-friendly defaults.
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./test.db")
# Unit tests must not share state through a real Redis (CI's service container or
# a local `docker compose` one): AI rate-limit counters would carry over between
# tests and runs and turn later requests into 429s. Port 1 refuses instantly,
# which exercises the fail-open paths; queue tests monkeypatch Redis anyway.
os.environ["REDIS_URL"] = "redis://127.0.0.1:1/0"
os.environ.setdefault("SECRET_KEY", "test-secret-key-32-bytes-min-len-for-tests")
# A developer's real key must never make tests call Mistral.
os.environ.pop("MISTRAL_API_KEY", None)

# In-memory SQLite for unit tests; integration tests use Postgres from docker-compose.
TEST_DB_URL = "sqlite+aiosqlite:///:memory:"

from app.core.config import get_settings  # noqa: E402

get_settings.cache_clear()  # type: ignore[attr-defined]
_settings = get_settings()
_settings.database_url = TEST_DB_URL
_settings.mistral_api_key = None  # also ignore one coming from a local .env

# Force models to use the in-memory DB.
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
    # Rooms outlive a test otherwise, and ids restart at 1 in the fresh DB, so
    # the next test's file 1 would read the previous test's live document.
    from app.realtime.manager import room_manager

    room_manager._rooms.clear()


@pytest_asyncio.fixture
async def db() -> AsyncGenerator[AsyncSession, None]:
    async with session_module.AsyncSessionLocal() as session:
        yield session


@pytest_asyncio.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
def no_op_enqueue(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    """Stub out the two Redis calls that run endpoints make: the execution
    queue push and the breakpoints-updated pub/sub broadcast.

    Without this, POST .../executions and PUT .../breakpoints both hit a real
    Redis connection. For executions that meant callers had to assert
    `status_code in (202, 500)` — which can't tell "queued" apart from
    "queuing is broken" — and for breakpoints it means the endpoint 500s
    outright with no real Redis available. Patching each queue/publish call at
    its call site makes both deterministic, and the returned list records each
    enqueue call's kwargs so tests can assert on what would have been sent to
    the worker (e.g. debug_session_info).

    `publish_project_event` is imported lazily inside upsert_breakpoints
    (`from app.realtime.execution_queue import publish_project_event`), so it
    must be patched on the defining module, not on app.api.breakpoints.
    """
    import app.api.executions as executions_module
    import app.realtime.execution_queue as execution_queue_module

    calls: list[dict] = []

    async def _fake_enqueue(*args: object, **kwargs: object) -> None:
        calls.append(kwargs)
        return None

    async def _fake_publish(*args: object, **kwargs: object) -> None:
        return None

    monkeypatch.setattr(executions_module, "enqueue_execution", _fake_enqueue)
    monkeypatch.setattr(execution_queue_module, "publish_project_event", _fake_publish)
    return calls


@pytest_asyncio.fixture
async def two_users(db: AsyncSession) -> tuple[dict, dict]:
    from app.core.security import create_access_token, hash_password
    from app.models import User

    # example.com (not .test) — some endpoints round-trip the email through
    # UserOut's EmailStr, which rejects RFC 2606 special-use domains like .test.
    a = User(username="alice", email=f"a-{uuid.uuid4().hex[:8]}@example.com", password_hash=hash_password("pw"))
    b = User(username="bob", email=f"b-{uuid.uuid4().hex[:8]}@example.com", password_hash=hash_password("pw"))
    db.add_all([a, b])
    await db.commit()
    for u in (a, b):
        await db.refresh(u)
    return (
        {"id": a.id, "username": a.username, "token": create_access_token(a.id)},
        {"id": b.id, "username": b.username, "token": create_access_token(b.id)},
    )


@pytest_asyncio.fixture
async def project(client: AsyncClient, two_users: tuple[dict, dict]) -> dict:
    """A project owned by `two_users`'s first user, with its bootstrap main.py file.

    Covers the project+file setup that used to be hand-rolled (against
    nonexistent /sessions/* routes) at the top of every permission/breakpoint/
    debug test.
    """
    a, b = two_users
    h = {"Authorization": f"Bearer {a['token']}"}
    r = await client.post("/projects", json={"name": "p1"}, headers=h)
    assert r.status_code == 201
    project_id = r.json()["id"]

    # create_project already bootstraps a main.py file — reuse it rather than
    # POSTing another "main.py" (would 409, the path is unique per project).
    r = await client.get(f"/projects/{project_id}/files", headers=h)
    assert r.status_code == 200
    file_id = r.json()[0]["id"]

    return {"id": project_id, "file_id": file_id, "owner": a, "other": b}


class FakeRedis:
    """Just the Redis calls the debugger coordination makes (app.debug.control)."""

    def __init__(self) -> None:
        self.data: dict[str, str] = {}
        self.published: list[tuple[str, dict]] = []
        self.listeners = 1  # pretend a worker is subscribed

    async def set(self, key, value, ex=None, nx=False):
        if nx and key in self.data:
            return None
        self.data[key] = value
        return True

    async def get(self, key):
        return self.data.get(key)

    async def delete(self, key):
        self.data.pop(key, None)

    async def publish(self, channel, message):
        import json as _json

        self.published.append((channel, _json.loads(message)))
        return self.listeners


@pytest.fixture
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> FakeRedis:
    """Debugger coordination and project events without a real Redis; events land in ``.published``."""
    import app.api.breakpoints as breakpoints_module
    import app.debug.control as control_module

    fake = FakeRedis()
    monkeypatch.setattr(control_module, "_redis", lambda: fake)

    async def _publish(project_id: int, event: dict) -> None:
        fake.published.append((f"project:{project_id}", event))

    monkeypatch.setattr(breakpoints_module, "publish_project_event", _publish)
    return fake
