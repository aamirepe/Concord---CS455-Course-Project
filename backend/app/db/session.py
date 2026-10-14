"""Async SQLAlchemy session/engine.

Note: the declarative ``Base`` lives in ``app.models.base`` (single source of
truth, used by Alembic and all ORM models). Do not re-import it through this
module — that would create a circular import (models -> session -> models).
"""
from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings

settings = get_settings()

# Pool sizing only applies to networked backends (Postgres via asyncpg).
# SQLite uses a single connection per process and rejects pool_size /
# max_overflow, so we skip those kwargs when the URL is sqlite.
_engine_kwargs: dict = {
    "echo": settings.app_env == "dev",
    "pool_pre_ping": True,
}
if not settings.database_url.startswith("sqlite"):
    _engine_kwargs["pool_size"] = 10
    _engine_kwargs["max_overflow"] = 20

engine = create_async_engine(settings.database_url, **_engine_kwargs)

AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session

__all__ = ["AsyncSessionLocal", "engine", "get_session"]
