"""One collaborative debug session of a Python program, run by the worker.

debugpy's *adapter* runs inside the usual sandbox container (no network,
read-only, non-root). We speak DAP to it over the container's stdin/stdout
(Docker's attach socket), and the adapter reaches the program over loopback
inside the container. Nothing is published on a port, so the sandbox keeps
having no network and worker replicas can't clash.

Everyone in the project sees the session through ``debug_*`` events; anyone
with the debugger role steps it through commands the backend relays over
Redis (see ``app.debug.control``). The handshake order matters: debugpy
answers ``launch`` only after ``configurationDone``, which may be sent only
after the ``initialized`` event.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.debug.dap import DapClient, DapProtocolError

log = logging.getLogger(__name__)

WORKSPACE = "/workspace"
MAX_VARIABLES = 100
MAX_VALUE_CHARS = 300
MAX_OUTPUT_CHARS = 200_000
HANDSHAKE_SECONDS = 30
# debugpy groups dunders, functions and classes under these; they only add noise.
HIDDEN_GROUPS = frozenset({"special variables", "function variables", "class variables"})


class AdapterProcess(Protocol):
    """Where debugpy's adapter runs; ``reader``/``writer`` are its stdout/stdin."""

    reader: asyncio.StreamReader
    writer: Any

    async def start(self) -> None: ...
    async def stop(self) -> None: ...


@dataclass
class DebugTarget:
    execution_id: int
    project_id: int
    file_id: int
    path: str  # the program, relative to the workspace
    breakpoints: dict[str, list[int]] = field(default_factory=dict)  # relative path -> lines
    file_ids: dict[str, int] = field(default_factory=dict)  # relative path -> file id
    user_id: int | None = None
    username: str | None = None


@dataclass
class DebugOutcome:
    exit_code: int | None
    stdout: str
    stderr: str
    duration_seconds: float
    reason: str  # "exited", "stopped", "timeout", "idle", "cancelled", "error"
    stopped_by: str | None = None
    error: str | None = None


def _clip(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


class DebugRun:
    def __init__(
        self,
        target: DebugTarget,
        adapter: AdapterProcess,
        publish: Callable[[dict], Awaitable[None]],
        commands: asyncio.Queue[dict],
        *,
        total_seconds: float = 600,
        idle_seconds: float = 180,
        save_state: Callable[[dict | None], Awaitable[None]] | None = None,
    ) -> None:
        self.t = target
        self.adapter = adapter
        self._publish = publish
        self.commands = commands
        self.total_seconds = total_seconds
        self.idle_seconds = idle_seconds
        self._save_state = save_state
        self.dap = DapClient()
        self._events: asyncio.Queue[dict] = asyncio.Queue()
        self.paused = False
        self.thread_id: int | None = None
        self.last_by: str | None = None
        self.exit_code: int | None = None
        self.stdout: list[str] = []
        self.stderr: list[str] = []
        self._out_chars = 0

    # --- helpers -----------------------------------------------------------

    def _abs(self, rel: str) -> str:
        return f"{WORKSPACE}/{rel.lstrip('/')}"

    def _rel(self, path: str | None) -> str | None:
        if path and path.startswith(WORKSPACE + "/"):
            return path[len(WORKSPACE) + 1:]
        return None

    async def publish(self, event_type: str, **fields: Any) -> None:
        try:
            await self._publish({"type": event_type, "execution_id": self.t.execution_id, **fields})
        except Exception:
            log.warning("could not publish %s", event_type, exc_info=True)

    async def _state(self, event: dict | None) -> None:
        if self._save_state is not None:
            try:
                await self._save_state(event)
            except Exception:
                log.warning("could not save debug state", exc_info=True)

    def _on_event(self, msg: dict) -> None:
        self._events.put_nowait(msg)

    # --- the session ---------------------------------------------------------

    async def run(self, cancel: asyncio.Event | None = None) -> DebugOutcome:
        cancel = cancel or asyncio.Event()
        started = time.monotonic()
        reason = "exited"
        stopped_by: str | None = None
        error: str | None = None
        try:
            await self.adapter.start()
            for ev in ("output", "stopped", "continued", "exited", "terminated", "thread", "_closed"):
                self.dap.on(ev, self._on_event)
            initialized = asyncio.Event()
            self.dap.on("initialized", lambda _m: initialized.set())
            self.dap.attach_streams(self.adapter.reader, self.adapter.writer)

            await asyncio.wait_for(self.dap.initialize(), HANDSHAKE_SECONDS)
            launch = await self.dap.start_request("launch", {
                "type": "python", "request": "launch", "program": self._abs(self.t.path),
                "cwd": WORKSPACE, "console": "internalConsole", "redirectOutput": True,
                "justMyCode": True, "stopOnEntry": False,
            })
            await asyncio.wait_for(initialized.wait(), HANDSHAKE_SECONDS)
            for rel, lines in self.t.breakpoints.items():
                await self.dap.set_breakpoints(self._abs(rel), sorted(set(lines)))
            await self.dap.configuration_done()
            DapClient.check("launch", await asyncio.wait_for(launch, HANDSHAKE_SECONDS))
            await self.publish("debug_started", file_id=self.t.file_id, path=self.t.path,
                               user_id=self.t.user_id, username=self.t.username)

            deadline = started + self.total_seconds
            idle_since = time.monotonic()
            while True:
                now = time.monotonic()
                if now >= deadline:
                    reason = "timeout"
                    break
                if self.paused and now - idle_since >= self.idle_seconds:
                    reason = "idle"
                    break
                wait = min(deadline - now, (idle_since + self.idle_seconds - now) if self.paused else 1.0, 1.0)
                getters: dict[asyncio.Task[Any], str] = {
                    asyncio.create_task(self._events.get()): "event",
                    asyncio.create_task(self.commands.get()): "command",
                    asyncio.create_task(cancel.wait()): "cancel",
                }
                done, pending = await asyncio.wait(getters, timeout=max(wait, 0.05), return_when=asyncio.FIRST_COMPLETED)
                for task in pending:
                    task.cancel()
                # A get() can complete in the same tick as another; don't lose it.
                results: list[tuple[str, Any]] = [(getters[t], t.result()) for t in done]
                for task in pending:
                    with contextlib.suppress(asyncio.CancelledError):
                        got = await task
                        results.append((getters[task], got))
                finished = False
                for kind, value in results:
                    if kind == "cancel":
                        reason, finished = "cancelled", True
                    elif kind == "event":
                        finished = await self._handle_event(value) or finished
                    else:
                        idle_since = time.monotonic()
                        if value.get("command") == "stop":
                            reason, stopped_by, finished = "stopped", value.get("username"), True
                        else:
                            await self._handle_command(value)
                    if kind == "event" and value.get("event") == "stopped":
                        idle_since = time.monotonic()
                if finished:
                    break
        except Exception as e:  # the adapter died, a timeout in the handshake, ...
            log.warning("debug session %d failed: %s", self.t.execution_id, e, exc_info=True)
            reason, error = "error", str(e) or type(e).__name__
        finally:
            if not self.dap.done:
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(self.dap.disconnect(terminate=True), 3)
            await self.dap.close()
            with contextlib.suppress(Exception):
                await self.adapter.stop()
            await self._state(None)

        outcome = DebugOutcome(
            exit_code=self.exit_code, stdout="".join(self.stdout), stderr="".join(self.stderr),
            duration_seconds=time.monotonic() - started, reason=reason, stopped_by=stopped_by, error=error,
        )
        await self.publish("debug_stopped", reason=reason, exit_code=self.exit_code, by=stopped_by, error=error)
        return outcome

    async def _handle_event(self, msg: dict) -> bool:
        """Returns True when the program is gone."""
        event = msg.get("event")
        body = msg.get("body") or {}
        if event == "output":
            category = body.get("category")
            if category in ("stdout", "stderr"):
                text = body.get("output", "")
                if self._out_chars < MAX_OUTPUT_CHARS:
                    text = text[: MAX_OUTPUT_CHARS - self._out_chars]
                    self._out_chars += len(text)
                    (self.stdout if category == "stdout" else self.stderr).append(text)
                    await self.publish("execution_output_chunk", stream=category, text=text)
        elif event == "thread" and body.get("reason") == "started":
            self.thread_id = self.thread_id or body.get("threadId")
        elif event == "stopped":
            self.paused = True
            self.thread_id = body.get("threadId") or self.thread_id
            state = await self._paused_state(body.get("reason", "pause"), body.get("text"))
            await self.publish("debug_paused", **state)
            await self._state({"type": "debug_paused", "execution_id": self.t.execution_id, **state})
        elif event == "continued":
            if self.paused:
                self.paused = False
                await self.publish("debug_running", by=self.last_by)
                await self._state(None)
        elif event == "exited":
            self.exit_code = body.get("exitCode")
        elif event in ("terminated", "_closed"):
            return True
        return False

    async def _paused_state(self, reason: str, text: str | None) -> dict:
        frames = await self.dap.stack_trace(self.thread_id) if self.thread_id is not None else []
        stack: list[dict[str, Any]] = []
        for fr in frames:
            rel = self._rel((fr.get("source") or {}).get("path"))
            if rel is None:
                continue  # library code: justMyCode keeps us out of it anyway
            stack.append({"id": fr.get("id"), "name": fr.get("name"), "line": fr.get("line"),
                          "path": rel, "file_id": self.t.file_ids.get(rel)})
        top = stack[0] if stack else None
        scopes, variables = await self._frame_scopes(top["id"]) if top else ([], [])
        return {
            "reason": reason, "description": text, "by": self.last_by,
            "path": top["path"] if top else None, "file_id": top["file_id"] if top else None,
            "line": top["line"] if top else None, "stack": stack, "scopes": scopes, "variables": variables,
        }

    async def _frame_scopes(self, frame_id: int) -> tuple[list[dict], list[dict]]:
        raw = await self.dap.scopes(frame_id)
        scopes = [{"name": s.get("name"), "ref": s.get("variablesReference")} for s in raw]
        first = await self._variables(scopes[0]["ref"]) if scopes and scopes[0]["ref"] else []
        return scopes, first

    async def _variables(self, ref: int) -> list[dict]:
        out = []
        for v in (await self.dap.variables(ref))[:MAX_VARIABLES]:
            if v.get("name") in HIDDEN_GROUPS:
                continue
            out.append({
                "name": v.get("name"), "value": _clip(str(v.get("value", "")), MAX_VALUE_CHARS),
                "type": v.get("type"), "ref": v.get("variablesReference") or 0,
            })
        return out

    async def _handle_command(self, cmd: dict) -> None:
        name = cmd.get("command")
        who = cmd.get("username")
        try:
            if name in ("continue", "next", "stepIn", "stepOut"):
                if not self.paused or self.thread_id is None:
                    return
                self.last_by = who
                await {"continue": self.dap.continue_, "next": self.dap.next,
                       "stepIn": self.dap.step_in, "stepOut": self.dap.step_out}[name](self.thread_id)
            elif name == "pause":
                if not self.paused:
                    self.last_by = who
                    await self.dap.pause(self.thread_id or 1)
            elif name == "variables":
                ref = int(cmd.get("ref") or 0)
                if self.paused and ref > 0:
                    await self.publish("debug_variables", ref=ref, variables=await self._variables(ref))
            elif name == "frame":
                frame_id = int(cmd.get("frame_id") or 0)
                if self.paused and frame_id:
                    scopes, variables = await self._frame_scopes(frame_id)
                    await self.publish("debug_frame", frame_id=frame_id, scopes=scopes, variables=variables)
            elif name == "setBreakpoints":
                rel = str(cmd.get("path") or "")
                if rel:
                    await self.dap.set_breakpoints(self._abs(rel), sorted({int(n) for n in cmd.get("lines") or []}))
        except (DapProtocolError, ConnectionError, TimeoutError, ValueError) as e:
            await self.publish("debug_message", text=f"{name} failed: {e}")
