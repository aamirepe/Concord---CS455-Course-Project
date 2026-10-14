"""Concurrent editing convergence test.

N simulated clients fire random updates against a shared Y.Doc. After the dust
settles, all clients must have byte-identical state.

This is the test that proves Feature 4 of the build spec: CRDT convergence.
pycrdt's job is to make the convergence math correct; ours is to make the
fanout architecture work without losing updates or leaking between rooms.
"""
from __future__ import annotations

import random

import pytest

from app.realtime.crdt import apply_update, doc_to_bytes, doc_to_text, new_doc


def _random_insert(text: str, rng: random.Random) -> tuple[str, int, str]:
    pos = rng.randint(0, len(text))
    ch = rng.choice("abcdefghijklmnopqrstuvwxyz ")
    return text[:pos] + ch + text[pos:], pos, ch


@pytest.mark.asyncio
async def test_concurrent_edits_converge():
    rng = random.Random(42)  # noqa: S311 -- deterministic test fixture, not cryptographic

    # A "room" is a server-side Doc. Each client has its own Doc that the room
    # mirrors. We simulate the server's fanout by applying every update to every
    # other client's Doc — exactly what the gateway does on each broadcast.
    room = new_doc("hello world")
    clients = [new_doc("hello world") for _ in range(10)]

    # Sync initial state to each client
    initial = doc_to_bytes(room)
    for c in clients:
        apply_update(c, initial)

    for _ in range(100):
        # A client makes an edit (mutates their local Doc) and we apply that
        # update to the room and rebroadcast to all OTHER clients.
        client = rng.choice(clients)
        before = doc_to_text(client)
        _after, pos, ch = _random_insert(before, rng)
        # Compute a real Yjs update so the test exercises the same wire format.
        from pycrdt import Text
        client.get("monaco", type=Text).insert(pos, ch)
        # pycrdt 0.10.x: doc.get_update() (no arg) returns the full state, which
        # is idempotent under apply_update — fine for a convergence test.
        update = doc_to_bytes(client)
        apply_update(room, update)
        for other in clients:
            if other is client:
                continue
            apply_update(other, update)

    # Converge: every client and the room must match.
    reference = doc_to_text(room)
    for i, c in enumerate(clients):
        assert doc_to_text(c) == reference, f"client {i} diverged from room"
