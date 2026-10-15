"""WebSocket gateway for collaborative editing.

Wire protocol (JSON over the WebSocket text channel, binary updates on a binary channel):

  client → server:
    {"op": "hello"}                    — initial connect, server sends full state back
    {"op": "sync_step1", "sv": "..."}  — client sends its state vector
    {"op": "awareness", "state": ...}  — cursor/presence
    {"op": "exec_event_ack", "id": N}  — client acknowledges an event id

  server → client (binary): a pycrdt state update. The first frame after
    connecting is the full document; a reconnecting client diffs it against
    its own doc and sends back only the edits the server is missing.

  server → client (text): JSON wrappers
    {"op": "full_state", "update_b64": "..."}     — sent on hello
    {"op": "exec_event", "event": {...}}           — forwarded from worker pub/sub
    {"op": "presence", "states": [...]}            — current awareness states
    {"op": "error", "message": "..."}              — e.g. permission denied
    {"op": "ack"}                                  — one per applied "update"

  server → project socket, from the checkpointer once an edit burst settles:
    {"op": "FILE_CHANGED", "file_id", "version", "changes": [{"user_id", "username", "lines_added", "lines_removed"}]}
    {"op": "project_presence", "user_id", "file_id"} — relayed when a client opens a file

  server → project socket (/ws/projects/{id}), progress of AI assistant runs
  (sent from the REST handler; no prompts, file contents or answers):
    {"op": "AI_STARTED", "request_id", "user_id", "username", "mode", "file_id"}
    {"op": "AI_STEP", "request_id", "user_id", "tool", "summary", "ok"}
    {"op": "AI_FINISHED", "request_id", "user_id", "ok", "has_proposal", "file_id"}

Edit propagation (binary frames) is the hot path. We serialize application of
updates under the room's lock so two near-simultaneous updates never interleave.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status
from sqlalchemy import select

from app.core.config import get_settings
from app.core.deps import _bearer  # noqa: F401  (token decoded in the handler)
from app.core.security import decode_token
from app.db.session import AsyncSessionLocal
from app.models import File, ProjectMember, User
from app.realtime.awareness import AwarenessError, decode_update, encode_update
from app.realtime.crdt import (
    diff_update,
    doc_to_bytes,
    state_vector,
)
from app.realtime.execution_queue import subscribe_project_events
from app.realtime.manager import Connection, _connection_writer, room_manager
from app.realtime.rooms import open_room

log = logging.getLogger(__name__)
settings = get_settings()

ws_router = APIRouter()

ACK = json.dumps({"op": "ack"})


async def _resolve_member(db, project_id: int, user_id: int) -> ProjectMember | None:
    res = await db.execute(
        select(ProjectMember).where(
            ProjectMember.project_id == project_id, ProjectMember.user_id == user_id
        )
    )
    return res.scalar_one_or_none()


def _role_rank(role: str) -> int:
    return {"viewer": 0, "editor": 1, "debugger": 2, "owner": 3}.get(role, 0)


@ws_router.websocket("/ws/projects/{project_id}")
async def project_socket(ws: WebSocket, project_id: int, token: str = ""):
    """Project-level socket for structural updates (files/folders) and project-wide events."""
    await ws.accept()

    try:
        payload = decode_token(token)
        user_id = int(payload["sub"])
    except (ValueError, KeyError):
        await ws.send_json({"op": "error", "message": "invalid token"})
        await ws.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    async with AsyncSessionLocal() as db:
        member = await _resolve_member(db, project_id, user_id)
        if member is None:
            await ws.send_json({"op": "error", "message": "not a project member"})
            await ws.close(code=status.WS_1008_POLICY_VIOLATION)
            return

    # Subscribe to execution events and structural updates for this project.
    conn = Connection(ws=ws, queue=asyncio.Queue())
    conn.writer_task = asyncio.create_task(_connection_writer(conn))
    room_manager._all_connections[ws] = conn
    room_manager.attach_to_project(project_id, ws)

    forwarder_task = asyncio.create_task(_forward_exec_events(
        conn,
        project_id
    ))

    try:
        while True:
            message = await ws.receive()
            if message["type"] == "websocket.disconnect":
                break
            # Project-level socket is primarily for receiving broadcasts, but we can handle heartbeats
            if "text" in message and message["text"] == "ping":
                await ws.send_text(json.dumps({"op": "pong"}))
    except Exception:
        log.exception("Project socket error")
    finally:
        forwarder_task.cancel()
        try:
            await forwarder_task
        except asyncio.CancelledError:
            pass

        room_manager.detach_from_project(project_id, ws)
        closed_conn = room_manager._all_connections.pop(ws, None)
        if closed_conn and closed_conn.writer_task:
            closed_conn.writer_task.cancel()

        log.info("Project socket disconnect user=%d project=%d", user_id, project_id)

@ws_router.websocket("/ws/projects/{project_id}/files/{file_id}")
async def collab_socket(ws: WebSocket, project_id: int, file_id: int, token: str = ""):
    """Collaborative editing socket. Token is passed as a query param because
    browsers can't set headers on the initial WebSocket upgrade."""
    await ws.accept()

    try:
        payload = decode_token(token)
        user_id = int(payload["sub"])
    except (ValueError, KeyError):
        await ws.send_json({"op": "error", "message": "invalid token"})
        await ws.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    async with AsyncSessionLocal() as db:
        member = await _resolve_member(db, project_id, user_id)
        if member is None:
            await ws.send_json({"op": "error", "message": "not a project member"})
            await ws.close(code=status.WS_1008_POLICY_VIOLATION)
            return

        file = await db.get(File, file_id)
        if not file:
            await ws.send_json({"op": "error", "message": "file not found"})
            await ws.close(code=status.WS_1008_POLICY_VIOLATION)
            return
        # Membership above was checked against the project in the URL, so the
        # file has to belong to that same project, or a member of project A could
        # open (and edit) a document of project B by guessing its file id.
        if file.project_id != project_id:
            await ws.send_json({"op": "error", "message": "file not found"})
            await ws.close(code=status.WS_1008_POLICY_VIOLATION)
            return
        user = await db.get(User, user_id)
        # The name on this person's cursor comes from the account, not the client.
        username = user.username if user else f"user-{user_id}"

    # The first connection loads the latest checkpoint into the room (under
    # its lock, so a simultaneous second connection waits for that instead of
    # being sent the empty doc). This is also how reconnect after a server
    # restart works.
    await open_room(project_id, file_id)

    room = await room_manager.attach(project_id, file_id, ws, user_id, member)
    log.info("WS connect user=%d project=%d file=%d", user_id, project_id, file_id)

    # Subscribe to execution events for this session and forward as text messages.
    forwarder_task = asyncio.create_task(_forward_exec_events(room.connections[ws], project_id))

    try:
        # Send initial full state to the new client.
        room.connections[ws].send(doc_to_bytes(room.doc))
        # ... and the cursors already in the file, so nobody has to wait for
        # their teammates to move before seeing where they are.
        existing = room.cursors.snapshot(exclude=ws)
        if existing:
            room.connections[ws].send(_awareness_frame(existing))

        last_activity = time.time()
        heartbeat = settings.websocket_heartbeat_interval

        while True:
            try:
                message = await asyncio.wait_for(ws.receive(), timeout=heartbeat)
            except TimeoutError:
                # Check if we've been silent for too long (2x heartbeat interval)
                if time.time() - last_activity > heartbeat * 2:
                    log.info("WS timeout: user %d silent for %ds, disconnecting", user_id, int(time.time() - last_activity))
                    break

                # Send a ping to check if client is still there
                try:
                    room.connections[ws].send(json.dumps({"op": "ping"}))
                except Exception:
                    break
                continue

            if message["type"] == "websocket.disconnect":
                break

            last_activity = time.time()

            if "bytes" in message and message["bytes"] is not None:
                # DISABLE binary frames to prevent double-application (we use Base64 JSON)
                log.debug("Ignoring binary update from user %d (use JSON)", user_id)
                continue

            if "text" in message and message["text"] is not None:
                try:
                    msg: dict[str, Any] = json.loads(message["text"])
                except json.JSONDecodeError:
                    room.connections[ws].send(json.dumps({"op": "error", "message": "invalid json"}))
                    continue

                op = msg.get("op")
                if op == "update" and _role_rank(member.role) < 1:
                    # Viewers are read-only. Their editor is read-only too, so
                    # this only triggers for a stale role or a hand-made client.
                    room.connections[ws].send(json.dumps({"op": "error", "message": "read-only: viewers can't edit"}))
                    continue
                if op == "update":
                    # Handle Base64 encoded CRDT updates
                    try:
                        b64_data = msg.get("data", "")
                        update = base64.b64decode(b64_data)

                        # DEDUPLICATION: Prevent applying the same update twice
                        # pycrdt/yjs updates are idempotent, but applying them
                        # multiple times in some versions/configs can cause issues.
                        # We use the room lock to ensure atomic application.
                        async with room.lock:
                            room.apply_edit(user_id, update)

                        log.debug("Applied update from user %d for room %d:%d. Size: %d bytes", user_id, room.project_id, room.file_id, len(update))
                        room_manager.broadcast(room, update, exclude=ws)
                        # Lets the client show "Syncing…" until the server has it.
                        room.connections[ws].send(ACK)
                    except Exception as e:
                        log.error("Error decoding Base64 update: %s", e)
                        room.connections[ws].send(json.dumps({"op": "error", "message": "invalid update format"}))
                    continue
                elif op == "awareness_update":
                    # Cursors and names. Decoded, size-limited and stamped with
                    # this socket's own identity before anyone else sees them.
                    try:
                        entries = decode_update(base64.b64decode(msg.get("data", ""), validate=True))
                    except (AwarenessError, ValueError, TypeError) as e:
                        log.warning("Dropping bad awareness update from user %d: %s", user_id, e)
                        continue
                    accepted = room.cursors.accept(ws, user_id, username, entries)
                    if accepted:
                        room_manager.broadcast(room, _awareness_frame(accepted), exclude=ws)
                    continue
                elif op == "sync_step1":
                    remote_sv_b64 = msg.get("sv", "")
                    remote_sv = base64.b64decode(remote_sv_b64) if remote_sv_b64 else state_vector(room.doc)
                    async with room.lock:
                        diff = diff_update(room.doc, remote_sv)
                    room.connections[ws].send(diff)
                elif op == "awareness":
                    state = msg.get("state") or {}
                    state.setdefault("user_id", user_id)
                    state.setdefault("username", "anon")
                    _set_awareness(room, ws, state)
                    # Echo the change to *other* clients only. Sending the
                    # sender its own state back causes the well-known Yjs
                    # "cursor flicker" bug.
                    _broadcast_presence(room, exclude=ws)
                elif op == "hello":
                    continue  # the full state was already sent on connect
                elif op == "project_presence":
                    # Which file each member has open, for the file tree.
                    room_manager.broadcast_to_project(
                        project_id, json.dumps({"op": "project_presence", "user_id": user_id, "file_id": file_id})
                    )
                elif op == "ping":
                    room.connections[ws].send(json.dumps({"op": "pong"}))
                elif op == "pong":
                    continue
                elif op in ("continue", "next", "stepIn", "stepOut"):
                    # Stepping moved to POST /projects/{id}/debug/{execution_id}/command.
                    room.connections[ws].send(json.dumps({"op": "error", "message": "use the Debug panel to step"}))
                else:
                    room.connections[ws].send(json.dumps({"op": "error", "message": f"unknown op: {op}"}))

    except WebSocketDisconnect:
        pass
    except Exception:
        log.exception("WS error")
    finally:
        room_manager.detach(room, ws)
        gone = room.cursors.drop(ws)
        if gone:
            # No goodbye needed from the client: a crashed tab loses its cursor too.
            room_manager.broadcast(room, _awareness_frame(gone))
        if not room_manager.has_user(room, user_id):
            # They closed the file (or the tab): the file tree and tabs stop
            # showing them there. ``left_file_id`` lets clients ignore this if
            # the user has meanwhile opened another file.
            room_manager.broadcast_to_project(project_id, json.dumps({
                "op": "project_presence", "user_id": user_id, "file_id": None, "left_file_id": file_id,
            }))
        forwarder_task.cancel()
        try:
            await forwarder_task
        except (asyncio.CancelledError, Exception):
            log.debug("forwarder task cleanup error", exc_info=True)
        log.info("WS disconnect user=%d project=%d file=%d", user_id, project_id, file_id)


def _awareness_frame(entries) -> str:
    return json.dumps({"op": "awareness_update", "data": base64.b64encode(encode_update(entries)).decode()})


def _set_awareness(room, ws, state: dict) -> None:
    """Store this connection's awareness state in the room's dict, keyed by
    the same uint32 client_id that yjs's awareness protocol uses."""
    from app.realtime.manager import _client_id_for  # avoid circular import

    cid = _client_id_for(ws)
    room.awareness[cid] = {**state, "client_id": cid}


def _broadcast_presence(room, exclude=None) -> None:
    """Send the current room awareness snapshot to other clients.

    ``exclude`` is the WebSocket of the sender (we don't echo presence back to
    the originator — that's the cursor-flicker bug).
    """
    # Build a list of (client_id, state) tuples; the frontend's
    # y-protocols/awareness layer expects the client_id inside each state.
    states = [
        {**state, "client_id": cid}
        for cid, state in room.awareness.items()
    ]
    msg = json.dumps({"op": "presence", "states": states})
    room_manager.broadcast(room, msg, exclude=exclude)


async def _forward_exec_events(conn: Connection, project_id: int) -> None:
    """Forward worker → project events (stdout, status changes) to the WS client."""
    log.info("Starting execution event forwarder for project %d", project_id)
    try:
        async for data in subscribe_project_events(project_id):
            try:
                log.debug("Forwarding exec event to project %d: %s", project_id, data)
                conn.send(
                    json.dumps({"op": "exec_event", "event": json.loads(data)})
                )
            except Exception:
                log.exception("Error sending exec event to WS")
                return
    except asyncio.CancelledError:
        log.info("Execution event forwarder cancelled for project %d", project_id)
        return
    except Exception:
        log.exception("forwarder crashed for project %d", project_id)
