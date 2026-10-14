"""Cursors and presence (NFR-U2): who is in a file, and where their cursor is.

The browser sends Yjs "awareness" updates (name, colour, selection) and the
server relays them. These tests pin down what the server must guarantee on top
of relaying:

* a person who opens a file sees the cursors already there, immediately,
  instead of waiting for each teammate to move;
* a connection that drops makes its cursor disappear for everyone else, instead
  of leaving a ghost behind until a client-side timeout;
* a client cannot present itself as someone else, and cannot send oversized or
  malformed awareness data.

The byte layout below is written out by hand from the y-protocols spec on
purpose, so these tests don't share a bug with the server's own encoder.
"""
from __future__ import annotations

import asyncio
import base64
import json
from typing import Any

import pytest
from httpx import AsyncClient
from httpx_ws import aconnect_ws
from httpx_ws.transport import ASGIWebSocketTransport

from app.realtime.awareness import (
    MAX_UPDATE_BYTES,
    AwarenessError,
    decode_update,
    encode_update,
)

SELECTION = {
    "anchor": {"type": None, "tname": "monaco", "item": None, "assoc": 0},
    "head": {"type": None, "tname": "monaco", "item": None, "assoc": 0},
}


def _var_uint(n: int) -> bytes:
    out = bytearray()
    while n > 0x7F:
        out.append(0x80 | (n & 0x7F))
        n >>= 7
    out.append(n)
    return bytes(out)


def _var_str(s: str) -> bytes:
    raw = s.encode()
    return _var_uint(len(raw)) + raw


def _raw_update(entries: list[tuple[int, int, Any]]) -> bytes:
    """Entries are (client_id, clock, state); state None means "gone"."""
    out = _var_uint(len(entries))
    for client_id, clock, state in entries:
        out += _var_uint(client_id) + _var_uint(clock) + _var_str(json.dumps(state, separators=(",", ":"), ensure_ascii=False))
    return out


def _frame(entries: list[tuple[int, int, Any]]) -> str:
    return json.dumps({"op": "awareness_update", "data": base64.b64encode(_raw_update(entries)).decode()})


def _entries(frame: dict) -> dict[int, tuple[int, Any]]:
    assert frame["op"] == "awareness_update"
    return {cid: (clock, state) for cid, clock, state in decode_update(base64.b64decode(frame["data"]))}


# --- the wire format ---------------------------------------------------------


def test_decode_reads_what_the_browser_encodes():
    state = {"user_id": 1, "username": "alice", "color": "#58a6ff", "selection": SELECTION}
    assert decode_update(_raw_update([(7, 3, state), (8, 1, None)])) == [(7, 3, state), (8, 1, None)]


def test_encode_matches_the_hand_written_bytes():
    entries = [(300, 129, {"a": 1}), (5, 2, None)]
    assert encode_update(entries) == _raw_update(entries)


@pytest.mark.parametrize(
    "data",
    [
        b"",  # nothing at all
        b"\x01",  # promises one entry, has none
        b"\x01\x05\x01",  # entry missing its state
        _var_uint(1) + _var_uint(1) + _var_uint(1) + _var_str("{not json"),
        _var_uint(1) + _var_uint(1) + _var_uint(1) + _var_str("[1, 2]"),  # state must be an object or null
        _var_uint(1) + _var_uint(2**32) + _var_uint(1) + _var_str("null"),  # client id doesn't fit in 32 bits
        _var_uint(99) + b"x",  # far too many entries
        b"\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\x01",  # a varint that never ends
        b"x" * (MAX_UPDATE_BYTES + 1),
    ],
)
def test_decode_rejects_malformed_or_oversized_updates(data: bytes):
    with pytest.raises(AwarenessError):
        decode_update(data)


# --- over a real WebSocket ---------------------------------------------------


async def _make_editor(client: AsyncClient, project: dict) -> None:
    r = await client.post(
        f"/projects/{project['id']}/members",
        json={"user_id": project["other"]["id"], "role": "editor"},
        headers={"Authorization": f"Bearer {project['owner']['token']}"},
    )
    assert r.status_code == 201


async def _next_json(ws, wanted_op: str) -> dict:
    for _ in range(20):
        raw = await asyncio.wait_for(ws.receive_text(), 2)
        msg = json.loads(raw)
        if msg.get("op") == wanted_op:
            return msg
    raise AssertionError(f"no {wanted_op} message")


async def _synced(ws) -> None:
    """Returns once the server has handled everything this socket sent before it."""
    await ws.send_text(json.dumps({"op": "ping"}))
    await _next_json(ws, "pong")


@pytest.mark.asyncio
async def test_a_newcomer_sees_cursors_that_are_already_in_the_file(client, project):
    await _make_editor(client, project)
    a, b = project["owner"], project["other"]
    from app.main import app

    url = f"/ws/projects/{project['id']}/files/{project['file_id']}"
    async with AsyncClient(transport=ASGIWebSocketTransport(app=app), base_url="http://test") as http:
        async with aconnect_ws(f"{url}?token={a['token']}", http) as alice:
            await alice.receive_bytes()
            await alice.send_text(_frame([(111, 4, {
                "user_id": a["id"], "username": "alice", "color": "#58a6ff", "selection": SELECTION,
            })]))
            await _synced(alice)

            async with aconnect_ws(f"{url}?token={b['token']}", http) as bob:
                await bob.receive_bytes()  # the document
                seen = _entries(await _next_json(bob, "awareness_update"))

    clock, state = seen[111]
    assert (clock, state["user_id"], state["username"], state["selection"]) == (4, a["id"], "alice", SELECTION)


@pytest.mark.asyncio
async def test_a_dropped_connection_takes_its_cursor_with_it(client, project):
    await _make_editor(client, project)
    a, b = project["owner"], project["other"]
    from app.main import app

    url = f"/ws/projects/{project['id']}/files/{project['file_id']}"
    async with AsyncClient(transport=ASGIWebSocketTransport(app=app), base_url="http://test") as http:
        async with aconnect_ws(f"{url}?token={a['token']}", http) as alice:
            await alice.receive_bytes()
            async with aconnect_ws(f"{url}?token={b['token']}", http) as bob:
                await bob.receive_bytes()
                await bob.send_text(_frame([(222, 1, {"user_id": b["id"], "username": "bob", "selection": SELECTION})]))
                assert _entries(await _next_json(alice, "awareness_update"))[222][1] is not None
            # Bob's socket closes without ever sending a goodbye (a crashed tab).
            gone = _entries(await _next_json(alice, "awareness_update"))

    clock, state = gone[222]
    assert state is None and clock > 1  # a newer clock, or Yjs would ignore the removal


@pytest.mark.asyncio
async def test_a_client_cannot_pose_as_someone_else(client, project):
    await _make_editor(client, project)
    a, b = project["owner"], project["other"]
    from app.main import app

    url = f"/ws/projects/{project['id']}/files/{project['file_id']}"
    async with AsyncClient(transport=ASGIWebSocketTransport(app=app), base_url="http://test") as http:
        async with aconnect_ws(f"{url}?token={a['token']}", http) as alice:
            await alice.receive_bytes()
            async with aconnect_ws(f"{url}?token={b['token']}", http) as bob:
                await bob.receive_bytes()
                # Bob claims to be the owner, with markup in the colour for good measure.
                await bob.send_text(_frame([(333, 1, {
                    "user_id": a["id"], "username": "alice (owner)", "color": "red;} body{display:none", "selection": SELECTION,
                })]))
                relayed = _entries(await _next_json(alice, "awareness_update"))

    state = relayed[333][1]
    assert state["user_id"] == b["id"] and state["username"] == b["username"]
    assert "color" not in state


@pytest.mark.asyncio
async def test_nobody_can_overwrite_another_users_cursor(client, project):
    await _make_editor(client, project)
    a, b = project["owner"], project["other"]
    from app.main import app

    url = f"/ws/projects/{project['id']}/files/{project['file_id']}"
    async with AsyncClient(transport=ASGIWebSocketTransport(app=app), base_url="http://test") as http:
        async with aconnect_ws(f"{url}?token={a['token']}", http) as alice:
            await alice.receive_bytes()
            await alice.send_text(_frame([(111, 1, {"selection": SELECTION})]))
            async with aconnect_ws(f"{url}?token={b['token']}", http) as bob:
                await bob.receive_bytes()
                await _next_json(bob, "awareness_update")  # alice's cursor, from the snapshot
                # Bob tries to move (and then erase) Alice's cursor, and sends one of his own.
                await bob.send_text(_frame([(111, 9, None), (444, 1, {"selection": SELECTION})]))
                relayed = _entries(await _next_json(alice, "awareness_update"))

    assert set(relayed) == {444}


@pytest.mark.asyncio
async def test_garbage_awareness_is_dropped_and_the_socket_stays_usable(client, project):
    a = project["owner"]
    from app.main import app

    url = f"/ws/projects/{project['id']}/files/{project['file_id']}"
    async with AsyncClient(transport=ASGIWebSocketTransport(app=app), base_url="http://test") as http:
        async with aconnect_ws(f"{url}?token={a['token']}", http) as alice:
            await alice.receive_bytes()
            await alice.send_text(json.dumps({"op": "awareness_update", "data": "!!not base64!!"}))
            await alice.send_text(json.dumps({"op": "awareness_update", "data": base64.b64encode(b"x" * 100_000).decode()}))
            await alice.send_text(json.dumps({"op": "awareness_update"}))
            await alice.send_text(json.dumps({"op": "ping"}))
            assert (await _next_json(alice, "pong"))["op"] == "pong"
