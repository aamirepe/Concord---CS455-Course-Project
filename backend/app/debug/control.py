"""Coordination between the backend and the worker running a debug session.

The worker owns the debug session (it talks DAP to debugpy inside the
sandbox). Everyone else reaches it through Redis:

  - ``concord:debug:active:{project_id}`` — the project's running debug
    session (at most one): ``{execution_id, file_id, path, user_id, username}``.
  - ``concord:debug:state:{execution_id}`` — the last "paused" event, so a
    member who opens the project mid-session sees where it stopped.
  - ``concord:debugcmd:{execution_id}`` — pub/sub channel of commands
    (continue, next, …) from the REST API to the worker.

Events go the other way through the usual project event channel
(``publish_project_event``) as ``debug_*`` events.
"""
from __future__ import annotations

import json
import logging
from time import time_ns
from typing import Any
from uuid import uuid4

from app.realtime.execution_queue import async_redis

log = logging.getLogger(__name__)

COMMAND_CHANNEL_PREFIX = "concord:debugcmd:"
ACTIVE_KEY = "concord:debug:active:{}"
STATE_KEY = "concord:debug:state:{}"
# Longer than the longest debug session, so a crashed worker's keys expire.
TTL_SECONDS = 20 * 60

STEP_COMMANDS = frozenset({"continue", "next", "stepIn", "stepOut", "pause", "stop"})
COMMANDS = STEP_COMMANDS | {"variables", "setBreakpoints"}


def _redis() -> Any:
    return async_redis()


async def set_active(project_id: int, info: dict) -> None:
    await _redis().set(ACTIVE_KEY.format(project_id), json.dumps(info), ex=TTL_SECONDS)


async def claim_active(project_id: int, info: dict) -> bool:
    """Mark ``info`` as the project's debug session unless one is running."""
    return bool(await _redis().set(ACTIVE_KEY.format(project_id), json.dumps(info), ex=TTL_SECONDS, nx=True))


async def get_active(project_id: int) -> dict | None:
    raw = await _redis().get(ACTIVE_KEY.format(project_id))
    return json.loads(raw) if raw else None


async def clear_active(project_id: int, execution_id: int) -> None:
    """Forget the project's debug session if it is still ``execution_id``."""
    active = await get_active(project_id)
    if active and active.get("execution_id") == execution_id:
        await _redis().delete(ACTIVE_KEY.format(project_id))
    await _redis().delete(STATE_KEY.format(execution_id))


async def set_state(execution_id: int, event: dict | None) -> None:
    key = STATE_KEY.format(execution_id)
    if event is None:
        await _redis().delete(key)
    else:
        await _redis().set(key, json.dumps(event), ex=TTL_SECONDS)


async def get_state(execution_id: int) -> dict | None:
    raw = await _redis().get(STATE_KEY.format(execution_id))
    return json.loads(raw) if raw else None


async def send_command(execution_id: int, command: dict) -> int:
    """Publish a command to the worker; returns how many listeners got it (0: none)."""
    command = {**command, "_timing": {"id": uuid4().hex, "published_ns": time_ns()}}
    listeners = int(await _redis().publish(COMMAND_CHANNEL_PREFIX + str(execution_id), json.dumps(command)))
    log.debug("debug_step stage=command_published execution=%s trace=%s at_ns=%s",
              execution_id, command["_timing"]["id"], command["_timing"]["published_ns"])
    return listeners
