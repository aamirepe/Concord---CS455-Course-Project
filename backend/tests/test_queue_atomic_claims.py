"""Concurrent dequeuers never claim the same execution twice (FR-15, NFR-D5).

Workers share one Redis hash of waiting executions. ``_pop_highest_priority``
reads the whole hash, picks the best entry and then HDELs it; only the caller
whose HDEL removes the field owns that execution. The fake Redis below yields
to the event loop inside every command, so many dequeuers really do read the
same snapshot and race for the same entry. HDEL on it is all-or-nothing, as on
a real server, so these tests cover our claim protocol and not Redis itself.
"""
import asyncio
import json
import time

import pytest

import app.realtime.execution_queue as queue
from app.realtime.execution_queue import QUEUE_META_HASH, _pop_highest_priority, dequeue_execution


class RacingRedis:
    """The few Redis commands the dequeue path uses, with a yield inside each."""

    def __init__(self) -> None:
        self.hashes: dict[str, dict[str, bytes]] = {}

    async def hset(self, name: str, key: str, value: str) -> int:
        await asyncio.sleep(0)
        self.hashes.setdefault(name, {})[key] = value.encode()
        return 1

    async def hgetall(self, name: str) -> dict[bytes, bytes]:
        await asyncio.sleep(0)
        return {k.encode(): v for k, v in self.hashes.get(name, {}).items()}

    async def hdel(self, name: str, key: str) -> int:
        await asyncio.sleep(0)
        return 1 if self.hashes.get(name, {}).pop(key, None) is not None else 0

    async def blpop(self, keys: list[str], timeout: int = 0) -> None:  # noqa: ASYNC109 -- mirrors redis-py
        await asyncio.sleep(0)  # a wake-up token is always available
        return None

    def waiting(self) -> int:
        return len(self.hashes.get(QUEUE_META_HASH, {}))


@pytest.fixture
def redis(monkeypatch: pytest.MonkeyPatch) -> RacingRedis:
    fake = RacingRedis()
    monkeypatch.setattr(queue, "async_redis", lambda: fake)
    return fake


async def _fill(redis: RacingRedis, count: int) -> None:
    roles = ["viewer", "editor", "debugger", "owner"]
    for execution_id in range(1, count + 1):
        payload = {
            "execution_id": execution_id,
            "project_id": 1,
            "role": roles[execution_id % len(roles)],
            "language": "python",
            "debug": False,
            "enqueued_at": time.time() - execution_id,
        }
        await redis.hset(QUEUE_META_HASH, str(execution_id), json.dumps(payload))


async def _drain(redis: RacingRedis, claimed: list[int], *, pop) -> None:
    """One worker: keep claiming until the queue is empty."""
    while redis.waiting():
        item = await pop()
        if item is not None:
            claimed.append(item["execution_id"])


@pytest.mark.asyncio
@pytest.mark.parametrize("dequeuers", [2, 8, 32])
async def test_concurrent_dequeuers_never_claim_the_same_execution(redis, dequeuers):
    total = 60
    await _fill(redis, total)

    claims: list[list[int]] = [[] for _ in range(dequeuers)]
    await asyncio.gather(*(_drain(redis, mine, pop=lambda: dequeue_execution(0.01)) for mine in claims))

    everything = [execution_id for mine in claims for execution_id in mine]
    assert len(everything) == len(set(everything)), "an execution was claimed by more than one worker"
    assert set(everything) == set(range(1, total + 1)), "an execution was never claimed"
    assert redis.waiting() == 0


@pytest.mark.asyncio
async def test_a_single_waiting_execution_goes_to_exactly_one_of_many_dequeuers(redis):
    await _fill(redis, 1)

    results = await asyncio.gather(*(_pop_highest_priority(redis) for _ in range(50)))

    winners = [r for r in results if r is not None]
    assert len(winners) == 1
    assert winners[0]["execution_id"] == 1
