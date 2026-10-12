"""Queue + pub/sub helpers around Redis.

Scheduling (FR-15): waiting executions live in a Redis hash, keyed by execution
id, holding everything needed to score them (role, debug flag, language,
enqueue time). A worker that wants its next job recomputes every waiting
execution's priority score and takes the highest one. This mirrors D1 SD-2
("Recompute the priority score of every waiting execution, then select the
waiting execution with the highest score") rather than a static per-item score
computed once at push time, because the wait-time ageing term has to grow
while an execution just sits there.

No slot is ever handed out twice: whichever worker's ``HDEL`` on the winning
id actually removes it is the one that runs it. Two workers racing on the same
snapshot just means the loser's ``HDEL`` returns 0 and it rescans — correctness
doesn't depend on exclusive access to the scan itself, only on that final,
atomic removal. A Redis list (``QUEUE_NOTIFY_LIST``) is kept purely so a worker
can block cheaply instead of busy-polling; the notify list carries no payload,
it only wakes someone up to go look at the hash.
"""
from __future__ import annotations

import asyncio
import json
import logging
import socket
import time
from collections.abc import Awaitable
from typing import Any, cast

import redis.asyncio as aioredis
from redis import Redis

from app.core.config import Settings, get_settings
from app.models.session import ROLE_RANK

log = logging.getLogger(__name__)

QUEUE_META_HASH = "concord:executions:meta"
QUEUE_NOTIFY_LIST = "concord:executions:notify"
CANCEL_CHANNEL_PREFIX = "concord:cancel:"
EXEC_EVENT_CHANNEL_PREFIX = "concord:exec:"  # pub/sub to room gateway
PRESENCE_CHANNEL_PREFIX = "concord:presence:"  # cross-process awareness

# Role weight reuses the same rank as permission checks (app.models.session.
# ROLE_RANK), normalised to 0..1 so it combines cleanly with the other
# (also 0..1-ish) terms in the priority formula. Re-keyed to plain strings:
# the payload coming back from Redis/JSON is a str, and ROLE_RANK's Role keys
# compare equal to it at runtime (Role is a StrEnum) but mypy can't see that.
_ROLE_RANK_BY_NAME: dict[str, int] = {role.value: rank for role, rank in ROLE_RANK.items()}
_MAX_ROLE_RANK = max(_ROLE_RANK_BY_NAME.values())


async def _await_redis(call: Any) -> Any:
    """redis-py's async client types most command methods as returning
    ``Awaitable[T] | T`` (to share stubs with the sync client), which mypy
    can't resolve under a bare ``await``. ``cast`` it to the async case —
    this is the same ambiguity ``app.llm.manager.check_rate_limit`` hits and
    casts around, just on the await side instead of the sync-result side."""
    return await cast(Awaitable[Any], call)

# Bounded so a pathological race (several workers repeatedly losing the same
# HDEL) can't spin forever; with worker_concurrency in the single digits this
# is far more retries than should ever be needed in practice.
_MAX_DEQUEUE_ATTEMPTS = 8


def _redis_sync() -> Redis:
    return Redis.from_url(get_settings().redis_url, decode_responses=False)


_async_redis: aioredis.Redis | None = None


def async_redis() -> aioredis.Redis:
    global _async_redis
    if _async_redis is None:
        _async_redis = aioredis.from_url(get_settings().redis_url, decode_responses=False)
    return _async_redis


def compute_priority(
    *,
    role: str,
    debug: bool,
    language: str,
    enqueued_at: float,
    now: float,
    settings: Settings | None = None,
) -> float:
    """The priority score from D1 Section 5.4. Pure function so it's unit-testable
    without Redis, and so the dequeue loop and tests can't silently drift apart."""
    settings = settings or get_settings()
    role_weight = _ROLE_RANK_BY_NAME.get(role, 0) / _MAX_ROLE_RANK
    if debug:
        role_weight += settings.priority_debug_bonus
    wait_term = min(max(now - enqueued_at, 0.0) / settings.priority_wait_saturation_seconds, 1.0)
    compile_cost = 1.0 if language == "cpp" else 0.0
    return (
        settings.priority_role_weight * role_weight
        + settings.priority_wait_weight * wait_term
        - settings.priority_lang_penalty_weight * compile_cost
    )


def _select_highest_priority(
    waiting: dict[str, dict[str, Any]], now: float, settings: Settings | None = None
) -> str | None:
    """``waiting`` maps execution id (str) to its decoded payload. Returns the id
    of the highest-priority entry, or None if ``waiting`` is empty. Split out
    from the Redis-calling code so it can be unit-tested directly."""
    best_id: str | None = None
    best_score: float | None = None
    for execution_id, payload in waiting.items():
        score = compute_priority(
            role=payload.get("role", "editor"),
            debug=bool(payload.get("debug", False)),
            language=payload.get("language", "python"),
            enqueued_at=payload["enqueued_at"],
            now=now,
            settings=settings,
        )
        if best_score is None or score > best_score:
            best_id, best_score = execution_id, score
    return best_id


def enqueue_execution_sync(
    execution_id: int,
    project_id: int,
    file_id: int,
    source_text: str,
    worker_id_hint: str | None = None,
    debug: bool = False,
    debug_session_info: dict | None = None,
    language: str = "python",
    stdin: str | None = None,
    role: str = "editor",
) -> None:
    """Synchronous enqueue — used from REST handlers and from worker entry points."""
    payload = {
        "execution_id": execution_id,
        "project_id": project_id,
        "file_id": file_id,
        "source_text": source_text,
        "worker_id_hint": worker_id_hint or socket.gethostname(),
        "debug": debug,
        "debug_session_info": debug_session_info,
        "language": language,
        "stdin": stdin,
        "role": role,
        "enqueued_at": time.time(),
    }
    r = _redis_sync()
    r.hset(QUEUE_META_HASH, str(execution_id), json.dumps(payload))
    r.lpush(QUEUE_NOTIFY_LIST, b"1")


async def enqueue_execution(
    execution_id: int,
    project_id: int,
    file_id: int,
    source_text: str,
    worker_id_hint: str | None = None,
    debug: bool = False,
    debug_session_info: dict | None = None,
    language: str = "python",
    stdin: str | None = None,
    role: str = "editor",
) -> None:
    # FastAPI handlers may be sync or async; we expose both.
    await asyncio.to_thread(
        enqueue_execution_sync,
        execution_id,
        project_id,
        file_id,
        source_text,
        worker_id_hint,
        debug,
        debug_session_info,
        language,
        stdin,
        role,
    )


async def dequeue_execution(wait_seconds: float = 1.0) -> dict[str, Any] | None:
    """Block up to ``wait_seconds`` for a wake-up, then take the
    highest-priority waiting execution, if any.

    ``wait_seconds`` bounds two things at once: how fast a newly-queued
    execution is picked up when a worker is already idle and blocked, and how
    stale the ageing term in the priority score can get for a worker that's
    been idle for a while with nothing arriving. Returns None on a timeout
    with nothing to run — callers should just loop.
    """
    r = async_redis()
    # redis-py's BLPOP timeout is an integer number of seconds; round up so a
    # sub-second budget still actually waits rather than returning instantly.
    await _await_redis(r.blpop([QUEUE_NOTIFY_LIST], timeout=max(1, int(wait_seconds + 0.999))))
    return await _pop_highest_priority(r)


async def _pop_highest_priority(r: aioredis.Redis) -> dict[str, Any] | None:
    settings = get_settings()
    for _ in range(_MAX_DEQUEUE_ATTEMPTS):
        raw = await _await_redis(r.hgetall(QUEUE_META_HASH))
        if not raw:
            return None
        waiting = {
            (k.decode() if isinstance(k, bytes) else k): json.loads(v)
            for k, v in raw.items()
        }
        best_id = _select_highest_priority(waiting, time.time(), settings)
        if best_id is None:
            return None
        # Whoever's HDEL actually removes the field wins it; a 0 means another
        # worker already took this one between our HGETALL and here, so we
        # just rescan rather than treating it as "nothing to run".
        removed = await _await_redis(r.hdel(QUEUE_META_HASH, best_id))
        if removed:
            return waiting[best_id]
    log.warning("Gave up finding a free execution after %d attempts (high contention?)", _MAX_DEQUEUE_ATTEMPTS)
    return None


async def remove_from_queue(execution_id: int) -> bool:
    """Drop a still-waiting execution from the queue, e.g. on cancel. Returns
    True if it was actually waiting (not yet picked up by a worker)."""
    r = async_redis()
    removed = await _await_redis(r.hdel(QUEUE_META_HASH, str(execution_id)))
    return bool(removed)


async def signal_cancel(execution_id: int) -> None:
    """Publish on the cancel channel. The running worker subscribes and kills the container."""
    r = async_redis()
    await r.publish(CANCEL_CHANNEL_PREFIX + str(execution_id), b"1")


async def publish_project_event(project_id: int, event: dict[str, Any]) -> None:
    """Used by execution workers to broadcast stdout/stderr/status to all connected clients
    in the project. The collab gateway subscribes to this channel and forwards to its room."""
    r = async_redis()
    await r.publish(EXEC_EVENT_CHANNEL_PREFIX + str(project_id), json.dumps(event).encode("utf-8"))


async def subscribe_project_events(project_id: int):
    """Async generator of (channel, bytes) — used by the WS gateway to forward to clients."""
    import logging
    log = logging.getLogger(__name__)
    log.info("Subscribing to project events for project %d", project_id)
    r = async_redis()
    pubsub = r.pubsub()
    await pubsub.subscribe(EXEC_EVENT_CHANNEL_PREFIX + str(project_id))
    log.info("Subscribed to channel %s", EXEC_EVENT_CHANNEL_PREFIX + str(project_id))
    try:
        async for msg in pubsub.listen():
            log.debug("Received pubsub message: %s", msg)
            if msg.get("type") != "message":
                continue
            yield msg["data"]
    finally:
        log.info("Unsubscribing from project events for project %d", project_id)
        await pubsub.unsubscribe()
        await pubsub.close()
