"""Cursors and presence on the server (NFR-U2).

Each browser announces who it is and where its cursor is with a Yjs
"awareness" update; the server's job is to pass those on. Passing them on blindly
has three problems this module fixes:

* Someone opening a file saw no cursors until each teammate next moved. The room
  now remembers every announcement and replays them to a newcomer.
* A connection that dropped left its cursor on everyone's screen until a
  client-side timeout. The room now announces the removal itself.
* Any member, viewers included, could send oversized or malformed data, claim to
  be another user, or overwrite another user's cursor. Updates are now decoded,
  size-limited, and stamped with the identity the socket authenticated as.

The wire format is the y-protocols one: a varint entry count, then per entry a
varint client id, a varint clock and a length-prefixed JSON state (``null``
means "gone"). A removal only counts if its clock is newer than the last one
seen, so the server announces removals at ``last clock + 1``.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from fastapi import WebSocket

AwarenessEntry = tuple[int, int, dict[str, Any] | None]

# A real update is one entry of a few hundred bytes; the limits only have to be
# generous for that and tight enough that a hostile client can't use this as a
# firehose to every collaborator.
MAX_UPDATE_BYTES = 8 * 1024
MAX_STATE_BYTES = 4 * 1024
MAX_ENTRIES_PER_UPDATE = 8
MAX_CLIENTS_PER_CONNECTION = 4

_UINT32 = 2**32
_MAX_VARINT_BITS = 28  # five bytes: enough for any uint32 id or clock
_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
# What y-monaco and the editor put in the state besides who the user is.
_PASSTHROUGH = ("selection", "cursor")


class AwarenessError(ValueError):
    """An awareness update that can't be trusted or understood."""


def _read_var_uint(data: bytes, pos: int) -> tuple[int, int]:
    result = shift = 0
    while True:
        if pos >= len(data):
            raise AwarenessError("truncated update")
        byte = data[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        if byte < 0x80:
            return result, pos
        shift += 7
        if shift > _MAX_VARINT_BITS:
            raise AwarenessError("number too long")


def _write_var_uint(n: int) -> bytes:
    out = bytearray()
    while n > 0x7F:
        out.append(0x80 | (n & 0x7F))
        n >>= 7
    out.append(n)
    return bytes(out)


def decode_update(data: bytes) -> list[AwarenessEntry]:
    """Parse an awareness update, rejecting anything oversized or malformed."""
    if len(data) > MAX_UPDATE_BYTES:
        raise AwarenessError("update too large")
    count, pos = _read_var_uint(data, 0)
    if count > MAX_ENTRIES_PER_UPDATE:
        raise AwarenessError("too many entries")
    entries: list[AwarenessEntry] = []
    for _ in range(count):
        client_id, pos = _read_var_uint(data, pos)
        clock, pos = _read_var_uint(data, pos)
        length, pos = _read_var_uint(data, pos)
        if client_id >= _UINT32 or clock >= _UINT32:
            raise AwarenessError("id or clock out of range")
        if length > MAX_STATE_BYTES or pos + length > len(data):
            raise AwarenessError("bad state length")
        try:
            state = json.loads(data[pos : pos + length].decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            raise AwarenessError("state is not JSON") from e
        pos += length
        if state is not None and not isinstance(state, dict):
            raise AwarenessError("state must be an object or null")
        entries.append((client_id, clock, state))
    if pos != len(data):
        raise AwarenessError("trailing bytes")
    return entries


def encode_update(entries: list[AwarenessEntry]) -> bytes:
    out = bytearray(_write_var_uint(len(entries)))
    for client_id, clock, state in entries:
        raw = json.dumps(state, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        out += _write_var_uint(client_id) + _write_var_uint(clock) + _write_var_uint(len(raw)) + raw
    return bytes(out)


def _clean_state(state: dict[str, Any], user_id: int, username: str) -> dict[str, Any]:
    """Who this cursor belongs to comes from the login, never from the client."""
    clean: dict[str, Any] = {"user_id": user_id, "username": username}
    color = state.get("color")
    if isinstance(color, str) and _COLOR.match(color):
        clean["color"] = color
    for key in _PASSTHROUGH:
        if key in state:
            clean[key] = state[key]
    return clean


@dataclass
class _Peer:
    clock: int
    state: dict[str, Any]
    ws: WebSocket
    user_id: int


@dataclass
class RoomAwareness:
    """The cursors currently shown in one file, keyed by Yjs client id."""

    peers: dict[int, _Peer] = field(default_factory=dict)

    def accept(self, ws: WebSocket, user_id: int, username: str, entries: list[AwarenessEntry]) -> list[AwarenessEntry]:
        """Record what ``ws`` announced and return what is safe to relay to the others."""
        accepted: list[AwarenessEntry] = []
        owned = sum(1 for p in self.peers.values() if p.ws is ws)
        for client_id, clock, state in entries:
            peer = self.peers.get(client_id)
            if peer is not None and peer.user_id != user_id:
                continue  # another user's cursor: not ours to move or erase
            if peer is not None and clock < peer.clock:
                continue  # an older announcement arriving late
            if state is None:
                if peer is None:
                    continue
                del self.peers[client_id]
                if peer.ws is ws:
                    owned -= 1
                accepted.append((client_id, clock, None))
                continue
            if peer is None:
                if owned >= MAX_CLIENTS_PER_CONNECTION:
                    continue
                owned += 1
            clean = _clean_state(state, user_id, username)
            # A second tab or a reconnect of the same user takes the cursor over.
            self.peers[client_id] = _Peer(clock, clean, ws, user_id)
            accepted.append((client_id, clock, clean))
        return accepted

    def snapshot(self, exclude: WebSocket | None = None) -> list[AwarenessEntry]:
        """Every cursor in the file, to bring a newcomer up to date."""
        return [(cid, p.clock, p.state) for cid, p in self.peers.items() if p.ws is not exclude]

    def drop(self, ws: WebSocket) -> list[AwarenessEntry]:
        """Forget everything ``ws`` owned; the result tells the others it is gone."""
        gone: list[AwarenessEntry] = []
        for client_id, peer in list(self.peers.items()):
            if peer.ws is ws:
                del self.peers[client_id]
                gone.append((client_id, peer.clock + 1, None))
        return gone
