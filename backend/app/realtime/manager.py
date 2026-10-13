"""Room registry: one Room per (project_id, file_id), each holding a CRDT Doc
and a dict of awareness states. Also owns the set of connected WebSockets per
room so we can fan out updates.

This is the single most important concurrency primitive in the app: every
WebSocket message handler in the gateway goes through it.

Awareness design
----------------
We do NOT use ``pycrdt.Awareness`` because:
1. pycrdt 0.9.x didn't export it at all, and 0.10.x's Awareness is bound to a
   single Y.Doc instance and is awkward to share between per-WS clients.
2. The y-protocols/awareness wire format that the yjs/y-monaco frontend speaks
   only needs a dict on the server side: ``{client_id: state_dict}``.

Each WebSocket connection is assigned a stable 32-bit ``client_id`` derived
from its id (truncated to 4 bytes for compatibility with Yjs's uint32 client
IDs). The room stores ``{client_id: state}``; the gateway broadcasts changes
to *other* clients only (never back to the sender, which would cause the
well-known awareness "cursor flicker" bug).
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.realtime.awareness import RoomAwareness
from app.realtime.changes import line_stats, split_stats
from app.realtime.crdt import Doc, apply_update_counting, doc_to_text, new_doc

if TYPE_CHECKING:
    from fastapi import WebSocket

    from app.models import ProjectMember

log = logging.getLogger(__name__)

# A different person editing after this long a pause starts a new attribution
# window (see Room.apply_edit).
AUTHOR_SWITCH_SECONDS = 1.0


def _client_id_for(ws: WebSocket) -> int:
    """Stable, uint32-compatible client id derived from the WebSocket identity.

    Yjs uses uint32 client IDs in the wire format; ``id()`` returns a 64-bit
    Python int so we mask it down. Two different WS objects in the same process
    will always have different ids (modulo collision, which is astronomically
    unlikely and recoverable by reconnect).
    """
    return id(ws) & 0xFFFFFFFF


@dataclass
class Connection:
    ws: WebSocket
    # Queue of messages (bytes or str) to be sent sequentially to the client.
    queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    writer_task: asyncio.Task | None = None
    user_id: int | None = None

    def send(self, payload: bytes | str) -> None:
        """Enqueue a message for serialized sending."""
        try:
            self.queue.put_nowait(payload)
        except asyncio.QueueFull:
            log.warning("Queue full for client %s, dropping message", self.ws)

@dataclass
class Room:
    project_id: int
    file_id: int
    doc: Doc
    # Per-client awareness state, keyed by the Yjs-compatible uint32 client id.
    # Each value is whatever the client most recently sent in its ``awareness``
    # message (cursor position, user info, selection, etc.).
    awareness: dict[int, dict[str, Any]] = field(default_factory=dict)
    # The cursors shown in this file (what ``awareness_update`` messages carry).
    cursors: RoomAwareness = field(default_factory=RoomAwareness)
    # Map client_id -> WebSocket so we can look up the connection that owns
    # each awareness state (needed for cleanup on disconnect and for skipping
    # the sender in broadcasts).
    client_to_ws: dict[int, WebSocket] = field(default_factory=dict)
    # Map WebSocket -> Connection (includes the send queue and writer task).
    connections: dict[WebSocket, Connection] = field(default_factory=dict)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    # Timestamp of the last time this room was accessed (for TTL cleanup)
    last_accessed: float = field(default_factory=lambda: time.time())
    # Whether the doc was loaded from the latest checkpoint (done once, by the
    # first connection, under ``lock``).
    hydrated: bool = False
    # Attribution window for the recent-changes panel: the text before its
    # first edit, how many characters each author changed, and when.
    # ``checkpoint.py`` closes it into ``file_changes`` rows.
    window_text: str | None = None
    window_authors: dict[int, int] = field(default_factory=dict)
    window_started: float = 0.0
    last_edit: float = 0.0
    last_author: int | None = None
    # Closed windows not yet written: user_id -> [lines_added, lines_removed].
    pending: dict[int, list[int]] = field(default_factory=dict)

    def apply_edit(self, user_id: int, update: bytes) -> None:
        """Apply ``user_id``'s CRDT update and record it in the window. Call under ``lock``."""
        now = time.time()
        if (
            self.window_authors
            and user_id != self.last_author
            and now - self.last_edit >= AUTHOR_SWITCH_SECONDS
        ):
            # People taking turns: credit the previous burst to its author
            # exactly, rather than sharing one diff between everyone.
            self.close_window()
        if not self.window_authors:
            self.window_text = doc_to_text(self.doc)
        changed = apply_update_counting(self.doc, update)
        if not changed:
            return  # e.g. an update the doc already had
        if not self.window_authors:
            self.window_started = now
        self.window_authors[user_id] = self.window_authors.get(user_id, 0) + changed
        self.last_edit = now
        self.last_author = user_id

    def close_window(self) -> None:
        """Turn the open window into line counts in ``pending``."""
        if self.window_authors:
            added, removed = line_stats(self.window_text or "", doc_to_text(self.doc))
            for uid, (a, r) in split_stats(self.window_authors, added, removed).items():
                acc = self.pending.setdefault(uid, [0, 0])
                acc[0] += a
                acc[1] += r
        self.reset_window()

    def take_pending(self) -> dict[int, tuple[int, int]]:
        """Closed line counts per author (skipping net-zero ones), clearing them."""
        out = {uid: (a, r) for uid, (a, r) in self.pending.items() if (a, r) != (0, 0)}
        self.pending = {}
        return out

    def reset_window(self) -> None:
        self.window_text = None
        self.window_authors = {}
        self.last_author = None


class RoomManager:
    def __init__(self) -> None:
        self._rooms: dict[tuple[int, int], Room] = {}
        self._global_lock = asyncio.Lock()
        # Registry for project-wide structural updates: project_id -> set of WebSockets
        self._project_rooms: dict[int, set[WebSocket]] = {}
        # Global map of WS -> Connection to allow project-wide broadcasts
        self._all_connections: dict[WebSocket, Connection] = {}

    async def get_or_create_room(self, project_id: int, file_id: int) -> Room:
        key = (project_id, file_id)
        room = self._rooms.get(key)
        if room is not None:
            room.last_accessed = time.time()
            return room
        async with self._global_lock:
            room = self._rooms.get(key)
            if room is not None:
                room.last_accessed = time.time()
                return room
            room = Room(project_id=project_id, file_id=file_id, doc=new_doc())
            self._rooms[key] = room
            log.info("Created room session=%d file=%d", project_id, file_id)
            return room

    def touch(self, room: Room) -> None:
        """Update the last accessed timestamp for a room."""
        room.last_accessed = time.time()

    def get_room(self, project_id: int, file_id: int) -> Room | None:
        return self._rooms.get((project_id, file_id))

    async def attach(self, project_id: int, file_id: int, ws: WebSocket, user_id: int, member: ProjectMember | None) -> Room:
        room = await self.get_or_create_room(project_id, file_id)
        self.touch(room)
        cid = _client_id_for(ws)

        # Create a connection and start its writer task
        conn = Connection(ws=ws, user_id=user_id)
        conn.writer_task = asyncio.create_task(_connection_writer(conn))

        room.connections[ws] = conn
        room.client_to_ws[cid] = ws
        self._all_connections[ws] = conn
        return room

    def detach(self, room: Room, ws: WebSocket) -> None:
        conn = room.connections.pop(ws, None)
        if conn and conn.writer_task:
            conn.writer_task.cancel()

        cid = _client_id_for(ws)
        room.client_to_ws.pop(cid, None)
        room.awareness.pop(cid, None)
        self._all_connections.pop(ws, None)
        if not room.connections:
            log.info("Room %d:%d has no live connections; keeping doc warm", room.project_id, room.file_id)

    @staticmethod
    def has_user(room: Room, user_id: int) -> bool:
        """Whether ``user_id`` still has a connection to this room (another tab, say)."""
        return any(c.user_id == user_id for c in room.connections.values())

    def broadcast(self, room: Room, payload: bytes | str, exclude: WebSocket | None = None) -> None:
        """Fire-and-forget broadcast of a CRDT update or JSON message."""
        log.debug("Broadcasting update to room %d:%d. Payload size: %d bytes. Clients: %d",
                 room.project_id, room.file_id, len(payload) if isinstance(payload, bytes | str) else 0, len(room.connections))
        for ws, conn in list(room.connections.items()):
            if ws is exclude:
                continue
            try:
                conn.queue.put_nowait(payload)
            except asyncio.QueueFull:
                log.warning("Queue full for client %s, dropping message", ws)

    def stats(self) -> dict:
        return {
            "rooms": len(self._rooms),
            "connections": sum(len(r.connections) for r in self._rooms.values()),
        }

    async def cleanup_unused_rooms(self, ttl_seconds: int = 300) -> int:
        """Remove rooms that have no active connections and have exceeded the TTL."""
        now = time.time()
        to_delete = []

        for key, room in list(self._rooms.items()):
            if not room.connections and (now - room.last_accessed) > ttl_seconds:
                to_delete.append(key)

        for key in to_delete:
            del self._rooms[key]
            log.info("Cleaned up inactive room %s", key)

        return len(to_delete)

    async def cleanup_loop(self, interval: int = 60, ttl: int = 300):
        """Background loop that periodically cleans up unused rooms."""
        log.info("Starting room cleanup loop (interval=%ds, ttl=%ds)", interval, ttl)
        while True:
            try:
                count = await self.cleanup_unused_rooms(ttl_seconds=ttl)
                if count > 0:
                    log.info("Room cleanup: removed %d rooms", count)
            except Exception:
                log.exception("Room cleanup loop crashed")
            await asyncio.sleep(interval)

    # --- Project Structural Sync Methods ---

    def attach_to_project(self, project_id: int, ws: WebSocket) -> None:
        """Subscribe a WebSocket to project-wide structural updates."""
        if project_id not in self._project_rooms:
            self._project_rooms[project_id] = set()
        self._project_rooms[project_id].add(ws)

    def detach_from_project(self, project_id: int, ws: WebSocket) -> None:
        """Unsubscribe a WebSocket from project updates."""
        if project_id in self._project_rooms:
            self._project_rooms[project_id].discard(ws)
            if not self._project_rooms[project_id]:
                del self._project_rooms[project_id]

    def broadcast_to_project(self, project_id: int, payload: str, exclude: WebSocket | None = None) -> None:
        """Broadcast a JSON message to all users currently active in the project."""
        ws_set = self._project_rooms.get(project_id, set())
        for ws in list(ws_set):
            if ws is exclude:
                continue
            conn = self._all_connections.get(ws)
            if conn:
                try:
                    conn.queue.put_nowait(payload)
                except asyncio.QueueFull:
                    log.warning("Queue full for project broadcast to client %s", ws)
            else:
                log.debug("No connection found for WS in project room %d", project_id)


async def _safe_send_bytes(ws: WebSocket, payload: bytes, timeout: float = 5.0) -> None:  # noqa: ASYNC109 -- timeout is a per-call send budget, not a task-scope deadline
    """Send bytes to a WebSocket with a timeout. Drops the message on failure.
    """
    try:
        await asyncio.wait_for(ws.send_bytes(payload), timeout=timeout)
    except (TimeoutError, asyncio.CancelledError):
        log.debug("safe_send: timeout/cancel, dropping message")
    except Exception:
        log.debug("safe_send: send failed", exc_info=True)

async def _safe_send_text(ws: WebSocket, payload: str, timeout: float = 5.0) -> None:  # noqa: ASYNC109 -- timeout is a per-call send budget, not a task-scope deadline
    """Send text to a WebSocket with a timeout. Drops the message on failure.
    """
    try:
        await asyncio.wait_for(ws.send_text(payload), timeout=timeout)
    except (TimeoutError, asyncio.CancelledError):
        log.debug("safe_send_text: timeout/cancel, dropping message")
    except Exception:
        log.debug("safe_send_text: send failed", exc_info=True)

async def _connection_writer(conn: Connection) -> None:
    """Dedicated loop per connection to serialize all outbound messages.
    This prevents interleaved frames and protocol corruption.
    """
    try:
        while True:
            # Wait for a message to send
            msg = await conn.queue.get()
            try:
                if isinstance(msg, bytes):
                    await _safe_send_bytes(conn.ws, msg)
                elif isinstance(msg, str):
                    await _safe_send_text(conn.ws, msg)
                else:
                    log.warning("Unsupported message type in queue: %s", type(msg))
            except Exception:
                log.debug("connection_writer: send failed, dropping message")
            finally:
                conn.queue.task_done()
    except asyncio.CancelledError:
        log.debug("connection_writer cancelled for ws %s", conn.ws)
    except Exception:
        log.exception("connection_writer crashed")


# Single global instance — the gateway attaches to it.
room_manager = RoomManager()
