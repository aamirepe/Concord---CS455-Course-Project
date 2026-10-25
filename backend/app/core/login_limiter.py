"""Fail-open Redis-backed throttling for repeated login failures."""
from __future__ import annotations

import hashlib
import logging

import redis.asyncio as aioredis
from redis.asyncio import Redis

from app.core.config import get_settings

log = logging.getLogger(__name__)

_redis_client: Redis | None = None


def _redis() -> Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = aioredis.from_url(get_settings().redis_url, decode_responses=True)
    return _redis_client


def _key(username: str) -> str:
    normalized = username.strip().casefold().encode("utf-8")
    return f"login:failures:{hashlib.sha256(normalized).hexdigest()}"


async def lockout_seconds_remaining(username: str) -> int:
    """Return the lockout TTL, or zero when login should be allowed."""
    settings = get_settings()
    try:
        client = _redis()
        count = int(await client.get(_key(username)) or 0)
        if count < settings.login_max_failures:
            return 0
        ttl = int(await client.ttl(_key(username)))
        if ttl > 0:
            return ttl
        await client.expire(_key(username), settings.login_lockout_seconds)
        return settings.login_lockout_seconds
    except Exception:
        log.warning("Redis unavailable while checking the login limit; allowing login", exc_info=True)
        return 0


async def record_login_failure(username: str) -> None:
    settings = get_settings()
    try:
        client = _redis()
        count = int(await client.incr(_key(username)))
        if count == 1:
            await client.expire(_key(username), settings.login_lockout_seconds)
    except Exception:
        log.warning("Redis unavailable while recording a login failure", exc_info=True)


async def reset_login_failures(username: str) -> None:
    try:
        await _redis().delete(_key(username))
    except Exception:
        log.warning("Redis unavailable while resetting login failures", exc_info=True)
