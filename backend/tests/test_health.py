"""Dependency readiness without authentication or leaked connection details."""
from unittest.mock import AsyncMock

import pytest

from app import main
from app.realtime.execution_queue import QUEUE_META_HASH


@pytest.fixture
def health_redis(monkeypatch):
    client = AsyncMock()
    client.hlen.return_value = 3
    monkeypatch.setattr(main, "async_redis", lambda: client)
    return client


async def test_health_all_dependencies_available(client, health_redis):
    response = await client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok", "service": "concord-backend",
        "database": "ok", "redis": "ok", "queue_depth": 3,
    }
    health_redis.hlen.assert_awaited_once_with(QUEUE_META_HASH)


@pytest.mark.parametrize("error", [ConnectionError("private connection details"), TimeoutError()])
async def test_health_redis_failure_is_degraded(client, health_redis, error):
    health_redis.ping.side_effect = error
    response = await client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["status"] == "degraded"
    assert response.json()["redis"] == "down"
    assert response.json()["queue_depth"] is None
    assert "private connection details" not in response.text


async def test_health_queue_failure_is_degraded(client, health_redis):
    health_redis.hlen.side_effect = ConnectionError()
    response = await client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["redis"] == "down"
    assert response.json()["queue_depth"] is None


@pytest.mark.parametrize("error", [ConnectionError(), TimeoutError()])
async def test_health_database_failure_is_unavailable(client, health_redis, monkeypatch, error):
    session = AsyncMock()
    session.__aenter__.side_effect = error
    monkeypatch.setattr(main.db_session, "AsyncSessionLocal", lambda: session)
    response = await client.get("/healthz")
    assert response.status_code == 503
    assert response.json()["status"] == "degraded"
    assert response.json()["database"] == "down"
    assert response.json()["redis"] == "ok"


async def test_health_bounds_a_stalled_redis(client, health_redis):
    import asyncio

    async def stalled_ping():
        await asyncio.Event().wait()

    health_redis.ping.side_effect = stalled_ping
    async with asyncio.timeout(3):
        response = await client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["redis"] == "down"


async def test_health_bounds_a_stalled_database_query(client, health_redis, monkeypatch):
    import asyncio

    cancelled = asyncio.Event()

    async def stalled_query(*args, **kwargs):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    session = AsyncMock()
    session.__aenter__.return_value = session
    session.execute.side_effect = stalled_query
    monkeypatch.setattr(main.db_session, "AsyncSessionLocal", lambda: session)

    async with asyncio.timeout(3):
        response = await client.get("/healthz")

    session.execute.assert_awaited_once()
    assert cancelled.is_set()
    assert response.status_code == 503
    assert response.json() == {
        "status": "degraded", "service": "concord-backend",
        "database": "down", "redis": "ok", "queue_depth": 3,
    }
