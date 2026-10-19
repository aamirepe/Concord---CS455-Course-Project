"""DebugRun against a scripted debug adapter (the real one runs in worker tests).

The fake answers ``launch`` only after ``configurationDone``, like debugpy, so
a client that awaits each request in turn would hang here.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
from types import SimpleNamespace

import pytest

from app.debug.session import DebugRun, DebugTarget


class FakeAdapter:
    def __init__(self, crash_on: str | None = None) -> None:
        self.reader = asyncio.StreamReader()
        self.writer = self
        self.requests: list[str] = []
        self.stopped = False
        self._seq = 1000
        self._launch_seq: int | None = None
        self._buf = b""
        self.crash_on = crash_on
        self.paused_line = 2

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        self.stopped = True
        if not self.reader.at_eof():
            self.reader.feed_eof()

    # the "stdin" side
    def write(self, data: bytes) -> None:
        self._buf += data
        while b"\r\n\r\n" in self._buf:
            head, rest = self._buf.split(b"\r\n\r\n", 1)
            n = int(head.split(b":")[1])
            if len(rest) < n:
                return
            body, self._buf = rest[:n], rest[n:]
            self._handle(json.loads(body))

    async def drain(self) -> None:
        pass

    def close(self) -> None:
        pass

    def _send(self, msg: dict) -> None:
        self._seq += 1
        body = json.dumps({"seq": self._seq, **msg}).encode()
        self.reader.feed_data(b"Content-Length: %d\r\n\r\n" % len(body) + body)

    def _reply(self, req: dict, body: dict | None = None, success: bool = True) -> None:
        self._send({"type": "response", "request_seq": req["seq"], "command": req["command"],
                    "success": success, "message": None if success else "nope", "body": body or {}})

    def _event(self, event: str, body: dict | None = None) -> None:
        self._send({"type": "event", "event": event, "body": body or {}})

    def _handle(self, req: dict) -> None:
        cmd = req["command"]
        self.requests.append(cmd)
        if cmd == self.crash_on:
            self.reader.feed_eof()
            return
        if cmd == "initialize":
            self._reply(req)
        elif cmd == "launch":
            self._launch_seq = req["seq"]
            self._event("initialized")
        elif cmd == "setBreakpoints":
            self._reply(req, {"breakpoints": []})
        elif cmd == "configurationDone":
            self._reply(req)
            self._reply({"seq": self._launch_seq, "command": "launch"})
            self._event("thread", {"reason": "started", "threadId": 1})
            self._event("output", {"category": "stdout", "output": "start\n"})
            self._event("output", {"category": "telemetry", "output": "x"})
            self._event("stopped", {"reason": "breakpoint", "threadId": 1})
        elif cmd == "stackTrace":
            self._reply(req, {"stackFrames": [
                {"id": 5, "name": "f", "line": self.paused_line, "source": {"path": "/workspace/lib/util.py"}},
                {"id": 6, "name": "run", "line": 9, "source": {"path": "/usr/lib/python3/runpy.py"}},
                {"id": 7, "name": "<module>", "line": 4, "source": {"path": "/workspace/main.py"}},
            ]})
        elif cmd == "scopes":
            self._reply(req, {"scopes": [{"name": "Locals", "variablesReference": 4}, {"name": "Globals", "variablesReference": 8}]})
        elif cmd == "variables":
            ref = req["arguments"]["variablesReference"]
            if ref == 4:
                self._reply(req, {"variables": [
                    {"name": "special variables", "value": "", "variablesReference": 30},
                    {"name": "xs", "value": "[1, 2]", "type": "list", "variablesReference": 9},
                    {"name": "big", "value": "y" * 1000, "type": "str", "variablesReference": 0},
                ]})
            else:
                self._reply(req, {"variables": [{"name": "0", "value": "1", "variablesReference": 0}]})
        elif cmd in ("next", "stepIn", "stepOut"):
            self._reply(req)
            self._event("continued", {"threadId": 1})
            self.paused_line += 1
            self._event("stopped", {"reason": "step", "threadId": 1})
        elif cmd == "pause":
            self._reply(req, success=False)
        elif cmd == "continue":
            self._reply(req)
            self._event("continued", {"threadId": 1})
            self._event("output", {"category": "stderr", "output": "warn\n"})
            self._event("exited", {"exitCode": 0})
            self._event("terminated")
        elif cmd == "disconnect":
            self._reply(req)
            self._event("terminated")
        else:
            self._reply(req)


def _session(adapter: FakeAdapter, **kw):
    events: list[dict] = []
    states: list[dict | None] = []

    async def publish(e: dict) -> None:
        events.append(e)

    async def save_state(s: dict | None) -> None:
        states.append(s)

    target = DebugTarget(execution_id=3, project_id=1, file_id=10, path="main.py",
                         breakpoints={"lib/util.py": [2, 2, 5]}, file_ids={"main.py": 10, "lib/util.py": 11},
                         user_id=1, username="dave")
    commands: asyncio.Queue[dict] = asyncio.Queue()
    run = DebugRun(target, adapter, publish, commands, save_state=save_state, **kw)
    return run, commands, events, states


async def _until(events: list[dict], kind: str, n: int = 1) -> dict:
    for _ in range(200):
        found = [e for e in events if e["type"] == kind]
        if len(found) >= n:
            return found[n - 1]
        await asyncio.sleep(0.01)
    raise AssertionError(f"no {kind} #{n} in {[e['type'] for e in events]}")


@pytest.mark.asyncio
async def test_a_whole_session():
    adapter = FakeAdapter()
    run, commands, events, states = _session(adapter)
    task = asyncio.create_task(run.run())

    paused = await _until(events, "debug_paused")
    assert adapter.requests[:5] == ["initialize", "launch", "setBreakpoints", "configurationDone", "stackTrace"]
    assert (paused["path"], paused["file_id"], paused["line"]) == ("lib/util.py", 11, 2)
    assert [f["path"] for f in paused["stack"]] == ["lib/util.py", "main.py"]  # library frames left out
    assert [v["name"] for v in paused["variables"]] == ["xs", "big"]
    assert len(paused["variables"][1]["value"]) == 300
    assert states[-1]["line"] == 2  # kept for late joiners

    commands.put_nowait({"command": "variables", "ref": 9})
    assert (await _until(events, "debug_variables"))["variables"] == [{"name": "0", "value": "1", "type": None, "ref": 0}]
    commands.put_nowait({"command": "frame", "frame_id": 7})
    assert (await _until(events, "debug_frame"))["frame_id"] == 7
    commands.put_nowait({"command": "next", "username": "bob"})
    assert (await _until(events, "debug_running"))["by"] == "bob"
    stepped = await _until(events, "debug_paused", 2)
    assert (stepped["line"], stepped["by"]) == (3, "bob")
    commands.put_nowait({"command": "pause", "username": "bob"})  # already paused: nothing happens
    commands.put_nowait({"command": "setBreakpoints", "path": "main.py", "lines": [4]})
    commands.put_nowait({"command": "continue", "username": "bob"})

    outcome = await asyncio.wait_for(task, 5)
    assert (outcome.reason, outcome.exit_code, outcome.stdout, outcome.stderr) == ("exited", 0, "start\n", "warn\n")
    assert [e["stream"] for e in events if e["type"] == "execution_output_chunk"] == ["stdout", "stderr"]
    assert events[0]["type"] == "debug_started" and events[-1]["type"] == "debug_stopped"
    assert states[-1] is None and adapter.stopped
    assert adapter.requests.count("setBreakpoints") == 2


@pytest.mark.asyncio
async def test_stop_cancel_idle_and_errors():
    adapter = FakeAdapter()
    run, commands, events, _ = _session(adapter)
    task = asyncio.create_task(run.run())
    await _until(events, "debug_paused")
    commands.put_nowait({"command": "stop", "username": "dave"})
    outcome = await asyncio.wait_for(task, 5)
    assert (outcome.reason, outcome.stopped_by) == ("stopped", "dave")
    assert "disconnect" in adapter.requests

    cancel = asyncio.Event()
    run, _, events, _ = _session(FakeAdapter())
    task = asyncio.create_task(run.run(cancel))
    await _until(events, "debug_paused")
    cancel.set()
    assert (await asyncio.wait_for(task, 5)).reason == "cancelled"

    run, _, _, _ = _session(FakeAdapter(), idle_seconds=0.2)
    assert (await asyncio.wait_for(run.run(), 5)).reason == "idle"

    run, _, _, _ = _session(FakeAdapter(), total_seconds=0.3)
    assert (await asyncio.wait_for(run.run(), 5)).reason == "timeout"

    run, _, events, _ = _session(FakeAdapter(crash_on="configurationDone"))
    outcome = await asyncio.wait_for(run.run(), 5)
    assert outcome.reason == "error" and outcome.error
    assert events[-1]["type"] == "debug_stopped" and events[-1]["error"]


@pytest.mark.asyncio
async def test_inspecting_variables_and_frames_does_not_reset_idle_timeout(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr("app.debug.session.time", SimpleNamespace(monotonic=lambda: clock[0]))
    run, commands, events, _ = _session(FakeAdapter())
    task = asyncio.create_task(run.run())
    try:
        await _until(events, "debug_paused")
        clock[0] = 100.0
        commands.put_nowait({"command": "variables", "ref": 9})
        await _until(events, "debug_variables")
        clock[0] = 179.0
        commands.put_nowait({"command": "frame", "frame_id": 7})
        await _until(events, "debug_frame")
        clock[0] = 181.0
        commands.put_nowait({"command": "variables", "ref": 9})
        assert (await asyncio.wait_for(asyncio.shield(task), 1)).reason == "idle"
    finally:
        if not task.done():
            task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


@pytest.mark.asyncio
async def test_a_failed_command_is_reported_not_fatal():
    adapter = FakeAdapter()
    run, commands, events, _ = _session(adapter)
    task = asyncio.create_task(run.run())
    await _until(events, "debug_paused")
    run.paused = False  # pretend it is running, so pause is sent (and refused by the fake)
    commands.put_nowait({"command": "pause", "username": "bob"})
    msg = await _until(events, "debug_message")
    assert msg["text"].startswith("pause failed")
    run.paused = True
    commands.put_nowait({"command": "stop"})
    assert (await asyncio.wait_for(task, 5)).reason == "stopped"


@pytest.mark.asyncio
async def test_step_location_does_not_wait_for_variables(monkeypatch):
    run, commands, events, _ = _session(FakeAdapter())
    original_scopes = run._frame_scopes
    inspecting = asyncio.Event()
    cancelled = asyncio.Event()
    calls = 0

    async def delayed_scopes(frame_id):
        nonlocal calls
        calls += 1
        if calls == 1:
            return await original_scopes(frame_id)
        inspecting.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(run, "_frame_scopes", delayed_scopes)
    task = asyncio.create_task(run.run())
    try:
        await _until(events, "debug_paused")
        commands.put_nowait({"command": "next", "username": "bob"})
        stepped = await _until(events, "debug_paused", 2)
        assert stepped["line"] == 3 and stepped["variables"] == []
        await asyncio.wait_for(inspecting.wait(), 1)
        commands.put_nowait({"command": "next", "username": "bob"})
        assert (await _until(events, "debug_paused", 3))["line"] == 4
        assert cancelled.is_set()
        assert not any(e["type"] == "debug_frame" for e in events)
        commands.put_nowait({"command": "stop"})
        assert (await asyncio.wait_for(task, 1)).reason == "stopped"
        assert run._details_task is None
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


@pytest.mark.asyncio
async def test_step_variables_update_the_frame_and_late_join_state():
    run, commands, events, states = _session(FakeAdapter())
    task = asyncio.create_task(run.run())
    try:
        await _until(events, "debug_paused")
        commands.put_nowait({"command": "next", "username": "bob"})
        paused = await _until(events, "debug_paused", 2)
        frame = await _until(events, "debug_frame")
        assert frame["frame_id"] == paused["stack"][0]["id"]
        assert [v["name"] for v in frame["variables"]] == ["xs", "big"]
        assert states[-1]["line"] == paused["line"] == 3
        assert states[-1]["variables"] == frame["variables"]
        assert states[-1]["scopes"] == frame["scopes"]
        commands.put_nowait({"command": "stop"})
        await asyncio.wait_for(task, 1)
        assert states[-1] is None
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
