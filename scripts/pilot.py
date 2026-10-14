"""Local pilot: seed demo data and check a running Concord stack end to end.

    python scripts/pilot.py seed    # demo users + "Pilot demo" project (idempotent)
    python scripts/pilot.py check   # automated checks against the running stack

Normally run through ``scripts/pilot.sh``, which executes this inside the
backend container (it has httpx, websockets and pycrdt), talking to the API on
localhost:8000. Everything goes through the public REST and WebSocket API, the
same way the browser does; nothing touches the database directly.

``seed`` never overwrites a file someone has already typed into, so it is safe
to re-run during a pilot. ``check`` works in its own project ("Pilot check")
and leaves the demo project alone.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import sys
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import httpx
import websockets
from pycrdt import Doc, Text

TEXT_TYPE = "monaco"  # the Y.Text name shared with the frontend's Monaco binding
PASSWORD = "pilot-pass-123"  # noqa: S105 -- shared demo password for local pilot accounts only
DEMO_PROJECT = "Pilot demo"
CHECK_PROJECT = "Pilot check"

# name -> role in the demo project (None: not a member, used for the join-request scenario)
USERS: dict[str, str | None] = {
    "alice": "owner",
    "bob": "editor",
    "carol": "viewer",
    "dave": "debugger",
    "erin": None,
}

DEMO_FILES = {
    # The bug the AI "Fix last error" scenario is built around: len(x) should be len(xs).
    "main.py": (
        "def average(xs):\n"
        "    total = 0\n"
        "    for x in xs:\n"
        "        total += x\n"
        "    return total / len(x)\n"
        "\n"
        "\n"
        "scores = [72, 85, 90, 64]\n"
        "print(\"Average score:\", average(scores))\n"
    ),
    "hello.cpp": (
        "#include <iostream>\n"
        "\n"
        "int main() {\n"
        "    for (int i = 1; i <= 3; ++i) {\n"
        "        std::cout << \"Hello from C++, line \" << i << std::endl;\n"
        "    }\n"
        "    return 0;\n"
        "}\n"
    ),
    "greet.js": (
        "const names = [\"alice\", \"bob\", \"carol\"];\n"
        "for (const n of names) {\n"
        "  console.log(`Hi ${n}!`);\n"
        "}\n"
    ),
}

TERMINAL = {"completed", "failed", "timeout", "cancelled"}


class Api:
    def __init__(self, base: str, ws_base: str) -> None:
        # No keep-alive: a failed step must not leave a dead pooled connection for the next one.
        self.http = httpx.AsyncClient(base_url=base, timeout=60, limits=httpx.Limits(max_keepalive_connections=0))
        self.ws_base = ws_base
        self.tokens: dict[str, str] = {}
        self.ids: dict[str, int] = {}

    async def close(self) -> None:
        await self.http.aclose()

    def h(self, user: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.tokens[user]}"}

    async def login_all(self) -> None:
        """Sign up each pilot user, or log in if the account already exists."""
        for name in USERS:
            r = await self.http.post("/auth/signup", json={
                "username": name, "email": f"{name}@example.com", "password": PASSWORD,
            })
            if r.status_code != 201:
                r = await self.http.post("/auth/login", json={"username": name, "password": PASSWORD})
            if r.status_code != 200 and r.status_code != 201:
                raise SystemExit(
                    f"Can't sign in as {name}: {r.status_code} {r.text[:200]}\n"
                    "If this account was made by hand with another password, run `scripts/pilot.sh reset`."
                )
            body = r.json()
            self.tokens[name] = body["access_token"]
            self.ids[name] = body["user"]["id"]

    async def project(self, owner: str, name: str) -> dict:
        r = await self.http.get("/projects", headers=self.h(owner))
        r.raise_for_status()
        for p in r.json():
            if p["name"] == name and p["owner_id"] == self.ids[owner]:
                return p
        r = await self.http.post("/projects", json={"name": name}, headers=self.h(owner))
        r.raise_for_status()
        return r.json()

    async def members(self, project_id: int, owner: str) -> dict[int, str]:
        r = await self.http.get(f"/projects/{project_id}/members", headers=self.h(owner))
        r.raise_for_status()
        return {m["user"]["id"]: m["role"] for m in r.json()}

    async def add_member(self, project_id: int, owner: str, user: str, role: str) -> None:
        current = await self.members(project_id, owner)
        uid = self.ids[user]
        if current.get(uid) == role:
            return
        if uid in current:
            r = await self.http.patch(
                f"/projects/{project_id}/members/{uid}", json={"role": role}, headers=self.h(owner)
            )
        else:
            r = await self.http.post(
                f"/projects/{project_id}/members", json={"user_id": uid, "role": role}, headers=self.h(owner)
            )
        if r.status_code >= 400:
            raise SystemExit(f"Can't give {user} the {role} role: {r.status_code} {r.text[:200]}")

    async def file(self, project_id: int, user: str, path: str) -> dict:
        r = await self.http.get(f"/projects/{project_id}/files", headers=self.h(user))
        r.raise_for_status()
        for f in r.json():
            if f["path"] == path:
                return f
        r = await self.http.post(f"/projects/{project_id}/files", json={"path": path}, headers=self.h(user))
        r.raise_for_status()
        return r.json()

    async def text(self, project_id: int, user: str, file_id: int) -> str:
        r = await self.http.get(f"/projects/{project_id}/files/{file_id}/content", headers=self.h(user))
        r.raise_for_status()
        return r.json().get("text") or ""

    def file_ws(self, project_id: int, file_id: int, user: str):
        return websockets.connect(
            f"{self.ws_base}/ws/projects/{project_id}/files/{file_id}?token={self.tokens[user]}",
            max_size=None,
        )

    def project_ws(self, project_id: int, user: str):
        return websockets.connect(f"{self.ws_base}/ws/projects/{project_id}?token={self.tokens[user]}")


async def _initial_doc(ws) -> Doc:
    """The server sends the full document as the first binary frame."""
    doc = Doc()
    doc.get(TEXT_TYPE, type=Text)
    while True:
        msg = await asyncio.wait_for(ws.recv(), timeout=10)
        if isinstance(msg, bytes):
            doc.apply_update(msg)
            return doc


async def _replace_text(ws, doc: Doc, new_text: str) -> None:
    """Replace the whole document, as one CRDT update sent like the browser does."""
    before = doc.get_state()
    text = doc.get(TEXT_TYPE, type=Text)
    with doc.transaction():
        if len(text):
            del text[0:len(text)]
        text.insert(0, new_text)
    update = doc.get_update(before)
    await ws.send(json.dumps({"op": "update", "data": base64.b64encode(update).decode()}))


async def set_file_text(api: Api, project_id: int, file_id: int, user: str, new_text: str) -> None:
    async with api.file_ws(project_id, file_id, user) as ws:
        doc = await _initial_doc(ws)
        await _replace_text(ws, doc, new_text)
        # Stay connected until the text is readable back; the server checkpoints
        # live rooms to the database every few seconds.
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if await api.text(project_id, user, file_id) == new_text:
                await asyncio.sleep(6)  # one checkpoint interval, so a restart keeps it
                return
            await asyncio.sleep(0.5)
        raise RuntimeError("the server never showed the new file content")


# --- seed --------------------------------------------------------------------

async def seed(api: Api) -> None:
    await api.login_all()
    project = await api.project("alice", DEMO_PROJECT)
    pid = project["id"]
    for user, role in USERS.items():
        if role not in (None, "owner"):
            await api.add_member(pid, "alice", user, role)

    written = []
    for path, content in DEMO_FILES.items():
        f = await api.file(pid, "alice", path)
        current = await api.text(pid, "alice", f["id"])
        # Fill empty files (a new project starts with an empty main.py);
        # never overwrite what a tester typed.
        if not current.strip():
            written.append(set_file_text(api, pid, f["id"], "alice", content))
    await asyncio.gather(*written)

    print(f"Seeded project \"{DEMO_PROJECT}\" (id {pid}); wrote {len(written)} file(s).")
    print("Accounts (password for all: " + PASSWORD + "):")
    for user, role in USERS.items():
        print(f"  {user:<6} id {api.ids[user]:<4} {role or 'not a member (for the join-request test)'}")
    print(f"PROJECT_ID={pid}")


# --- check -------------------------------------------------------------------

@dataclass
class Result:
    name: str
    status: str  # PASS / FAIL / SKIP
    detail: str
    seconds: float


async def run_file(api: Api, pid: int, fid: int, user: str, limit: float = 90, stdin: str | None = None) -> dict:
    body: dict = {"file_id": fid}
    if stdin is not None:
        body["stdin"] = stdin
    r = await api.http.post(f"/projects/{pid}/executions", json=body, headers=api.h(user))
    if r.status_code != 202:
        raise RuntimeError(f"Run was refused: {r.status_code} {r.text[:200]}")
    ex = r.json()
    deadline = time.monotonic() + limit
    while ex["status"] not in TERMINAL:
        if time.monotonic() > deadline:
            raise RuntimeError(
                f"still '{ex['status']}' after {limit:.0f}s. Is the worker running? "
                "See `scripts/pilot.sh logs worker`."
            )
        await asyncio.sleep(1)
        r = await api.http.get(f"/projects/{pid}/executions/{ex['id']}", headers=api.h(user))
        r.raise_for_status()
        ex = r.json()
    return ex


def _out(ex: dict) -> str:
    return f"status={ex['status']} exit={ex.get('exit_code')} stdout={ex['stdout'][-120:]!r} stderr={ex['stderr'][-160:]!r}"


async def check(api: Api, only: set[str] | None) -> int:
    results: list[Result] = []

    async def step(name: str, fn: Callable[[], Awaitable[str]]) -> None:
        if only and not any(o in name.lower() for o in only):
            return
        started = time.monotonic()
        try:
            detail = await fn()
            status = "SKIP" if detail.startswith("SKIP:") else "PASS"
            detail = detail.removeprefix("SKIP:").strip()
        except Exception as e:  # report and keep going: the pilot wants the full picture
            status, detail = "FAIL", f"{type(e).__name__}: {e}"
        results.append(Result(name, status, detail, time.monotonic() - started))
        print(f"  [{status}] {name} ({time.monotonic() - started:.1f}s) {detail}", flush=True)

    ctx: dict = {}

    async def api_up() -> str:
        r = await api.http.get("/healthz")
        r.raise_for_status()
        return f"/healthz -> {r.json()}"

    async def accounts() -> str:
        await api.login_all()
        p = await api.project("alice", CHECK_PROJECT)
        ctx["pid"] = p["id"]
        for user, role in (("bob", "editor"), ("carol", "viewer"), ("dave", "debugger")):
            await api.add_member(p["id"], "alice", user, role)
        for name in ("check.py", "check_fail.py", "check.cpp", "check.js", "check_loop.py", "check_net.py",
                     "check_input.py", "check_history.py", "check_snap_a.py", "check_snap_b.py",
                     "check_latency.txt", "check_debug.py"):
            ctx[name] = (await api.file(p["id"], "alice", name))["id"]
        return f"logged in as {', '.join(USERS)}; check project id {p['id']}"

    async def collab() -> str:
        pid, fid = ctx["pid"], ctx["check.py"]
        marker = f"# collab {uuid.uuid4().hex[:8]}\n"
        async with api.file_ws(pid, fid, "alice") as a, api.file_ws(pid, fid, "bob") as b:
            adoc, bdoc = await _initial_doc(a), await _initial_doc(b)
            started = time.monotonic()
            await _replace_text(a, adoc, marker)
            while str(bdoc.get(TEXT_TYPE, type=Text)) != marker:
                msg = await asyncio.wait_for(b.recv(), timeout=5)
                if isinstance(msg, bytes):
                    bdoc.apply_update(msg)
            return f"bob saw alice's edit after {(time.monotonic() - started) * 1000:.0f} ms"

    async def latency() -> str:
        from latency import measure  # scripts/latency.py, next to this file

        pid, fid = ctx["pid"], ctx["check_latency.txt"]
        users = ("alice", "bob", "dave")
        urls = [f"{api.ws_base}/ws/projects/{pid}/files/{fid}?token={api.tokens[u]}" for u in users]
        result = await measure(urls, edits_per_client=50, interval=0.05)
        s = result.summary()
        detail = (f"{s['delivered']}/{s['expected']} edits seen by the others; "
                  f"p50 {s['p50_ms']} ms, p99 {s['p99_ms']} ms, max {s['max_ms']} ms (target p99 < 300 ms)")
        await set_file_text(api, pid, fid, "alice", "")  # tidy up the tokens
        if not result.ok:
            raise AssertionError(detail + (f"; errors: {s['errors']}" if s["errors"] else ""))
        return detail

    async def save_and_history() -> str:
        pid, fid = ctx["pid"], ctx["check.js"]
        new_text = f"// saved by bob {uuid.uuid4().hex[:8]}\nconsole.log('hi');\n"
        async with api.project_ws(pid, "alice") as feed, api.file_ws(pid, fid, "bob") as b:
            doc = await _initial_doc(b)
            await _replace_text(b, doc, new_text)
            while True:  # the server acknowledges each edit
                msg = await asyncio.wait_for(b.recv(), timeout=5)
                if isinstance(msg, str) and json.loads(msg).get("op") == "ack":
                    break
            r = await api.http.post(f"/projects/{pid}/files/{fid}/save", headers=api.h("bob"))
            r.raise_for_status()
            saved = r.json()
            if not saved["created"]:
                raise AssertionError(f"Save made no new version: {saved}")
            while True:  # alice hears about it on the project socket
                event = json.loads(await asyncio.wait_for(feed.recv(), timeout=10))
                if event.get("op") == "FILE_CHANGED" and event["file_id"] == fid:
                    break
        r = await api.http.get(f"/projects/{pid}/activity", headers=api.h("alice"))
        r.raise_for_status()
        entry = next((f for f in r.json() if f["file_id"] == fid), None)
        bob = entry and next((a for a in entry["authors"] if a["username"] == "bob"), None)
        if not bob:
            raise AssertionError(f"check.js/bob missing from recent changes: {r.json()}")
        return (f"version {saved['version']}; alice got FILE_CHANGED; "
                f"recent changes: bob +{bob['lines_added']} -{bob['lines_removed']}")

    async def viewer_read_only() -> str:
        pid, fid = ctx["pid"], ctx["check.py"]
        async with api.file_ws(pid, fid, "carol") as c:
            doc = await _initial_doc(c)
            await _replace_text(c, doc, "carol was here\n")
            while True:
                msg = await asyncio.wait_for(c.recv(), timeout=5)
                if isinstance(msg, str) and json.loads(msg).get("op") == "error":
                    err = json.loads(msg)["message"]
                    break
        if "carol was here" in await api.text(pid, "alice", fid):
            raise AssertionError("the viewer's edit was applied")
        return f"edit refused: {err!r}"

    async def run_python() -> str:
        pid, fid = ctx["pid"], ctx["check.py"]
        await set_file_text(api, pid, fid, "bob", "print('sum is', sum(range(10)))\n")
        events: list[str] = []

        async def listen() -> None:
            async with api.file_ws(pid, fid, "alice") as ws:
                while True:
                    msg = await ws.recv()
                    if isinstance(msg, str) and json.loads(msg).get("op") == "exec_event":
                        events.append(json.loads(msg)["event"].get("status") or json.loads(msg)["event"].get("type"))

        listener = asyncio.create_task(listen())
        await asyncio.sleep(1)
        try:
            ex = await run_file(api, pid, fid, "bob")
            await asyncio.sleep(1)
        finally:
            listener.cancel()
        if ex["status"] != "completed" or "sum is 45" not in ex["stdout"]:
            raise AssertionError(_out(ex))
        live = f"; live events on the editor socket: {events}" if events else (
            "; WARNING: no live status events reached the editor socket (Redis pub/sub?)"
        )
        return f"printed {ex['stdout'].strip()!r}{live}"

    async def run_stdin() -> str:
        pid, fid = ctx["pid"], ctx["check_input.py"]
        await set_file_text(api, pid, fid, "bob", "a = int(input())\nb = int(input())\nprint('total', a + b)\n")
        ex = await run_file(api, pid, fid, "bob", stdin="3\n4\n")
        if ex["status"] != "completed" or "total 7" not in ex["stdout"]:
            raise AssertionError(_out(ex))
        ex = await run_file(api, pid, fid, "bob")  # no input: EOF, not a hang
        if ex["status"] == "timeout" or "EOFError" not in ex["stderr"]:
            raise AssertionError(f"without input: {_out(ex)}")
        return "read '3', '4' and printed 'total 7'; without input the program got EOF"

    async def debug_together() -> str:
        """dave debugs, bob watches: pause at a breakpoint, variables, step, finish."""
        pid, fid = ctx["pid"], ctx["check_debug.py"]
        await set_file_text(api, pid, fid, "dave", "def add(a, b):\n    total = a + b\n    return total\n\n"
                            "s = 0\nfor x in [1, 2, 3]:\n    s = add(s, x)\nprint('sum', s)\n")
        r = await api.http.put(f"/projects/{pid}/files/{fid}/breakpoints", json={"lines": [3]}, headers=api.h("dave"))
        r.raise_for_status()

        async def next_event(ws, kind: str) -> dict:
            while True:
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=40))
                event = msg.get("event") if msg.get("op") == "exec_event" else None
                if event and event.get("type") == kind:
                    return event

        async with api.project_ws(pid, "bob") as watch:
            await asyncio.sleep(0.5)  # subscribed before anything happens
            r = await api.http.post(f"/projects/{pid}/executions", json={"file_id": fid, "debug": True}, headers=api.h("dave"))
            if r.status_code != 202:
                raise AssertionError(f"debug start refused: {r.status_code} {r.text[:200]}")
            ex_id = r.json()["id"]
            cmd = f"/projects/{pid}/debug/{ex_id}/command"
            try:
                paused = await next_event(watch, "debug_paused")
                local = {v["name"]: v["value"] for v in paused["variables"]}
                if (paused["line"], local.get("total")) != (3, "1"):
                    raise AssertionError(f"paused at line {paused['line']} with {local}")
                late = (await api.http.get(f"/projects/{pid}/debug", headers=api.h("carol"))).json()
                if (late.get("paused") or {}).get("line") != 3:
                    raise AssertionError(f"a late joiner doesn't see the pause: {late}")
                r = await api.http.post(cmd, json={"command": "next"}, headers=api.h("bob"))
                if r.status_code != 403:
                    raise AssertionError(f"an editor could step: {r.status_code}")
                (await api.http.post(cmd, json={"command": "next"}, headers=api.h("dave"))).raise_for_status()
                stepped = await next_event(watch, "debug_paused")
                if stepped.get("by") != "dave":
                    raise AssertionError(f"step not attributed: {stepped.get('by')}")
                await api.http.put(f"/projects/{pid}/files/{fid}/breakpoints", json={"lines": []}, headers=api.h("dave"))
                (await api.http.post(cmd, json={"command": "continue"}, headers=api.h("dave"))).raise_for_status()
                stopped = await next_event(watch, "debug_stopped")
            finally:
                await api.http.post(cmd, json={"command": "stop"}, headers=api.h("dave"))
        ex = (await api.http.get(f"/projects/{pid}/executions/{ex_id}", headers=api.h("dave"))).json()
        for _ in range(20):
            if ex["status"] in TERMINAL:
                break
            await asyncio.sleep(0.5)
            ex = (await api.http.get(f"/projects/{pid}/executions/{ex_id}", headers=api.h("dave"))).json()
        if stopped.get("reason") != "exited" or ex["status"] != "completed" or "sum 6" not in (ex["stdout"] or ""):
            raise AssertionError(f"finish: {stopped}, {_out(ex)}")
        return (f"bob saw dave's session pause at line 3 (total=1), a step by dave, and the end ('sum 6'); "
                f"carol, joining late, saw the pause; bob (editor) couldn't step")

    async def history_restore() -> str:
        pid, fid = ctx["pid"], ctx["check_history.py"]
        await set_file_text(api, pid, fid, "alice", "good = 1\n")
        r = await api.http.post(f"/projects/{pid}/files/{fid}/save", headers=api.h("alice"))
        r.raise_for_status()
        good_version = r.json()["version"]
        await set_file_text(api, pid, fid, "alice", "broken = (\n")
        async with api.file_ws(pid, fid, "bob") as b:  # bob has the file open during the restore
            doc = await _initial_doc(b)
            r = await api.http.post(f"/projects/{pid}/files/{fid}/versions/{good_version}/restore", headers=api.h("alice"))
            r.raise_for_status()
            while str(doc.get(TEXT_TYPE, type=Text)) != "good = 1\n":
                msg = await asyncio.wait_for(b.recv(), timeout=5)
                if isinstance(msg, bytes):
                    doc.apply_update(msg)
        versions = (await api.http.get(f"/projects/{pid}/files/{fid}/versions?saves_only=true", headers=api.h("bob"))).json()
        who = versions[0]["saved_by"]["username"] if versions and versions[0]["saved_by"] else None
        if who != "alice":
            raise AssertionError(f"latest save should be alice's restore: {versions[:2]}")
        return f"restored v{good_version}; bob's open editor got it live; history shows {len(versions)} saves"

    async def snapshot_restore() -> str:
        pid, a, b = ctx["pid"], ctx["check_snap_a.py"], ctx["check_snap_b.py"]
        await set_file_text(api, pid, a, "alice", "A = 'before'\n")
        await set_file_text(api, pid, b, "alice", "B = 'before'\n")
        r = await api.http.post(f"/projects/{pid}/snapshots", json={"label": f"pilot {uuid.uuid4().hex[:6]}"}, headers=api.h("alice"))
        r.raise_for_status()
        snap = r.json()
        await set_file_text(api, pid, a, "bob", "A = 'after'\n")
        await set_file_text(api, pid, b, "bob", "B = 'after'\n")
        r = await api.http.post(f"/projects/{pid}/snapshots/{snap['id']}/restore", headers=api.h("alice"))
        r.raise_for_status()
        texts = [await api.text(pid, "alice", fid) for fid in (a, b)]
        if texts != ["A = 'before'\n", "B = 'before'\n"]:
            raise AssertionError(f"after restore: {texts}")
        return f"snapshot of {snap['file_count']} files; restored {r.json()['restored_files']} changed ones (with a safety snapshot)"

    async def run_error() -> str:
        pid, fid = ctx["pid"], ctx["check_fail.py"]
        await set_file_text(api, pid, fid, "bob", "print(undefined_name)\n")
        ex = await run_file(api, pid, fid, "bob")
        if ex["status"] != "failed" or "NameError" not in ex["stderr"]:
            raise AssertionError(_out(ex))
        return "failed with NameError in stderr, as expected"

    async def run_cpp() -> str:
        pid, fid = ctx["pid"], ctx["check.cpp"]
        await set_file_text(api, pid, fid, "bob", DEMO_FILES["hello.cpp"])
        ex = await run_file(api, pid, fid, "bob", limit=120)
        if ex["status"] != "completed" or "Hello from C++, line 3" not in ex["stdout"]:
            raise AssertionError(_out(ex))
        return "compiled and ran"

    async def run_js() -> str:
        pid, fid = ctx["pid"], ctx["check.js"]
        await set_file_text(api, pid, fid, "bob", DEMO_FILES["greet.js"])
        ex = await run_file(api, pid, fid, "bob")
        if ex["status"] != "completed" or "Hi carol!" not in ex["stdout"]:
            raise AssertionError(_out(ex))
        return "ran with node"

    async def sandbox_timeout() -> str:
        pid, fid = ctx["pid"], ctx["check_loop.py"]
        await set_file_text(api, pid, fid, "bob", "while True:\n    pass\n")
        ex = await run_file(api, pid, fid, "bob", limit=120)
        if ex["status"] != "timeout":
            raise AssertionError(_out(ex))
        return "infinite loop was stopped with status 'timeout'"

    async def sandbox_network() -> str:
        pid, fid = ctx["pid"], ctx["check_net.py"]
        await set_file_text(api, pid, fid, "bob", (
            "import urllib.request\n"
            "try:\n"
            "    urllib.request.urlopen('http://example.com', timeout=3)\n"
            "    print('NETWORK OPEN')\n"
            "except Exception as e:\n"
            "    print('blocked:', type(e).__name__)\n"
        ))
        ex = await run_file(api, pid, fid, "bob")
        if "NETWORK OPEN" in ex["stdout"] or "blocked:" not in ex["stdout"]:
            raise AssertionError(_out(ex))
        return ex["stdout"].strip()

    async def viewer_cannot_run() -> str:
        r = await api.http.post(
            f"/projects/{ctx['pid']}/executions", json={"file_id": ctx["check.py"]}, headers=api.h("carol")
        )
        if r.status_code != 403:
            raise AssertionError(f"expected 403, got {r.status_code} {r.text[:120]}")
        return "403 for a viewer"

    async def ai() -> str:
        pid = ctx["pid"]
        st = (await api.http.get(f"/projects/{pid}/assist/status", headers=api.h("alice"))).json()
        if not st.get("configured"):
            return "SKIP: no MISTRAL_API_KEY in .env (the AI panel will ask the owner for a key)"
        await set_file_text(api, pid, ctx["check.py"], "alice", "def area(r):\n    return 3.14159 * r * r\n")
        request_id = uuid.uuid4().hex
        seen: list[str] = []
        async with api.project_ws(pid, "bob") as watcher:
            post = asyncio.create_task(api.http.post(
                f"/projects/{pid}/assist", headers=api.h("alice"), timeout=120,
                json={"prompt": "Explain this file in one sentence.", "mode": "explain",
                      "file_id": ctx["check.py"], "request_id": request_id},
            ))
            while not seen or seen[-1] != "AI_FINISHED":
                msg = json.loads(await asyncio.wait_for(watcher.recv(), timeout=120))
                if msg.get("request_id") == request_id:
                    seen.append(msg["op"])
            r = await post
        if r.status_code != 200:
            raise AssertionError(f"{r.status_code} {r.text[:200]}")
        body = r.json()
        return (f"{body['model_used']}, {body['latency_ms']} ms, {body['total_tokens']} tokens; "
                f"bob's socket saw {seen}")

    print("Concord pilot check")
    await step("API is up", api_up)
    await step("Pilot accounts and check project", accounts)
    if "pid" not in ctx:
        print("Stopping: could not set up the accounts.")
        return 1
    await step("Real-time co-editing (alice -> bob)", collab)
    await step("Edit latency, 3 users typing (p99)", latency)
    await step("Save + recent changes", save_and_history)
    await step("Version history + restore", history_restore)
    await step("Snapshot + restore", snapshot_restore)
    await step("Viewer edits are refused", viewer_read_only)
    await step("Viewer cannot run code", viewer_cannot_run)
    await step("Run Python", run_python)
    await step("Run Python with an error", run_error)
    await step("Program input (stdin)", run_stdin)
    await step("Debug together (breakpoint, variables, step)", debug_together)
    await step("Run C++ (compile + run)", run_cpp)
    await step("Run JavaScript", run_js)
    await step("Sandbox stops infinite loops", sandbox_timeout)
    await step("Sandbox has no network", sandbox_network)
    await step("AI assistant + live progress events", ai)

    counts = {s: sum(r.status == s for r in results) for s in ("PASS", "FAIL", "SKIP")}
    print(f"\n{counts['PASS']} passed, {counts['FAIL']} failed, {counts['SKIP']} skipped")
    return 1 if counts["FAIL"] else 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("command", choices=["seed", "check"])
    parser.add_argument("--api", default="http://localhost:8000")
    parser.add_argument("--ws", default="ws://localhost:8000")
    parser.add_argument("--only", nargs="*", help="check: run only steps whose name contains one of these words")
    args = parser.parse_args()

    async def go() -> int:
        api = Api(args.api, args.ws)
        try:
            if args.command == "seed":
                await seed(api)
                return 0
            return await check(api, {o.lower() for o in args.only} if args.only else None)
        finally:
            await api.close()

    try:
        sys.exit(asyncio.run(go()))
    except httpx.ConnectError:
        sys.exit(f"Can't reach the API at {args.api}. Is the stack up? Try `scripts/pilot.sh status`.")


if __name__ == "__main__":
    main()
