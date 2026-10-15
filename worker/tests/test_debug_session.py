"""The collaborative debugger against the real sandbox image (DOCKER_TESTS=1).

debugpy's adapter runs inside the sandbox with no network; we drive it the
way users do, through the command queue, and look at the events everyone
would see.
"""
from __future__ import annotations

import asyncio
import os
import tempfile

import pytest

from app.debug.docker_adapter import DockerAdapter
from app.debug.session import DebugRun, DebugTarget
from app.services.sandbox import prepare_workspace, sandbox

DOCKER_TESTS = os.environ.get("DOCKER_TESTS") == "1"
IMAGE = "concord-sandbox:latest"

PROGRAM = """\
from helper import add

xs = [1, 2, 3]
s = 0
for x in xs:
    s = add(s, x)
print("sum", s)
"""
HELPER = """\
def add(a, b):
    total = a + b
    return total
"""


class Session:
    """A debug run in the background, with the events it published."""

    def __init__(self, workspace: str, breakpoints: dict[str, list[int]], **kw) -> None:
        self.events: list[dict] = []
        self.commands: asyncio.Queue[dict] = asyncio.Queue()
        self.new_event = asyncio.Event()
        target = DebugTarget(execution_id=1, project_id=1, file_id=10, path="main.py",
                             breakpoints=breakpoints, file_ids={"main.py": 10, "helper.py": 11},
                             user_id=1, username="alice")
        self.run = DebugRun(target, DockerAdapter(sandbox.client, IMAGE, workspace), self._publish,
                            self.commands, **kw)

    async def _publish(self, event: dict) -> None:
        self.events.append(event)
        self.new_event.set()

    async def wait_for(self, kind: str, count: int = 1, timeout: float = 40) -> dict:
        async def _wait() -> dict:
            while True:
                found = [e for e in self.events if e["type"] == kind]
                if len(found) >= count:
                    return found[count - 1]
                self.new_event.clear()
                await self.new_event.wait()
        return await asyncio.wait_for(_wait(), timeout)

    def send(self, command: str, **kw) -> None:
        self.commands.put_nowait({"command": command, "username": "bob", **kw})


def _workspace() -> str:
    d = tempfile.mkdtemp(prefix="concord-debug-test-")
    for name, text in (("main.py", PROGRAM), ("helper.py", HELPER)):
        with open(os.path.join(d, name), "w") as f:
            f.write(text)
    prepare_workspace(d)
    return d


@pytest.mark.skipif(not DOCKER_TESTS, reason="set DOCKER_TESTS=1 to run")
@pytest.mark.asyncio
async def test_breakpoint_variables_stepping_and_output():
    s = Session(_workspace(), {"helper.py": [3]})
    task = asyncio.create_task(s.run.run())

    paused = await s.wait_for("debug_paused")
    assert (paused["path"], paused["file_id"], paused["line"], paused["reason"]) == ("helper.py", 11, 3, "breakpoint")
    assert [f["path"] for f in paused["stack"]] == ["helper.py", "main.py"]
    assert {v["name"]: v["value"] for v in paused["variables"]} == {"a": "0", "b": "1", "total": "1"}

    # Look at the caller's frame, then expand the list there.
    s.send("frame", frame_id=paused["stack"][1]["id"])
    frame = await s.wait_for("debug_frame")
    xs = next(v for v in frame["variables"] if v["name"] == "xs")
    assert xs["value"] == "[1, 2, 3]" and xs["ref"] > 0
    s.send("variables", ref=xs["ref"])
    items = await s.wait_for("debug_variables")
    assert [v["value"] for v in items["variables"] if v["name"].isdigit()] == ["1", "2", "3"]

    s.send("next")
    stepped = await s.wait_for("debug_paused", 2)
    assert stepped["line"] == 3 or stepped["path"] == "main.py"  # stepped out of add() or onto the next line
    assert stepped["by"] == "bob"

    s.send("continue")  # stops at the breakpoint again (second call of add)
    again = await s.wait_for("debug_paused", 3)
    assert {v["name"]: v["value"] for v in again["variables"]}["a"] == "1"

    s.commands.put_nowait({"command": "setBreakpoints", "path": "helper.py", "lines": []})
    s.send("continue")
    outcome = await asyncio.wait_for(task, 40)
    assert outcome.reason == "exited" and outcome.exit_code == 0
    assert outcome.stdout == "sum 6\n"
    assert any(e["type"] == "execution_output_chunk" for e in s.events)
    assert s.events[-1]["type"] == "debug_stopped"


@pytest.mark.skipif(not DOCKER_TESTS, reason="set DOCKER_TESTS=1 to run")
@pytest.mark.asyncio
async def test_stop_and_idle_timeout_end_the_session():
    s = Session(_workspace(), {"main.py": [4]})
    task = asyncio.create_task(s.run.run())
    await s.wait_for("debug_paused")
    s.send("stop")
    outcome = await asyncio.wait_for(task, 30)
    assert (outcome.reason, outcome.stopped_by) == ("stopped", "bob")

    s = Session(_workspace(), {"main.py": [4]}, idle_seconds=2)
    outcome = await asyncio.wait_for(s.run.run(), 60)
    assert outcome.reason == "idle"
    # The container is gone.
    assert not sandbox.client.containers.list(all=True, filters={"ancestor": IMAGE, "status": "running"})


@pytest.mark.skipif(not DOCKER_TESTS, reason="set DOCKER_TESTS=1 to run")
@pytest.mark.asyncio
async def test_cancel_and_a_program_that_crashes():
    ws = _workspace()
    with open(os.path.join(ws, "main.py"), "w") as f:
        f.write("print('before')\nraise SystemExit(3)\n")
    s = Session(ws, {})
    outcome = await asyncio.wait_for(s.run.run(), 60)
    assert (outcome.reason, outcome.exit_code, outcome.stdout) == ("exited", 3, "before\n")

    s = Session(_workspace(), {"main.py": [4]})
    cancel = asyncio.Event()
    task = asyncio.create_task(s.run.run(cancel))
    await s.wait_for("debug_paused")
    cancel.set()
    assert (await asyncio.wait_for(task, 30)).reason == "cancelled"
