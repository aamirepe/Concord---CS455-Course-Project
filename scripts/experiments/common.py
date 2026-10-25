"""REST clients and timestamped project events for workflow measurements."""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import math
import os
import statistics
import time
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode, urlsplit, urlunsplit

import httpx
from pycrdt import Doc, Text
from websockets.legacy.client import WebSocketClientProtocol, connect

TERMINAL_STATUSES = {"completed", "failed", "timeout", "cancelled"}
PILOT_PASSWORD = "pilot-pass-123"  # noqa: S105 -- existing local demo accounts


@dataclass(frozen=True)
class Event:
    received_at: str
    monotonic_s: float
    message: dict[str, Any]

    @property
    def payload(self) -> dict[str, Any]:
        if self.message.get("op") == "exec_event":
            return self.message["event"]
        return self.message


class EventLog:
    def __init__(self) -> None:
        self.events: list[Event] = []
        self.ready = asyncio.Event()
        self.closed = False
        self.error: str | None = None
        self.changed = asyncio.Condition()

    async def read(self, socket: WebSocketClientProtocol) -> None:
        try:
            async for raw in socket:
                arrived = time.perf_counter()
                received_at = datetime.now(UTC).isoformat()
                if isinstance(raw, bytes):
                    continue
                message = json.loads(raw)
                if not isinstance(message, dict):
                    raise ValueError("Project event must be an object")
                if message.get("op") == "pong":
                    self.ready.set()
                    continue
                if message.get("op") == "error":
                    raise RuntimeError("Project socket rejected the request")
                async with self.changed:
                    self.events.append(Event(received_at, arrived, message))
                    self.changed.notify_all()
        except Exception as exc:
            self.error = f"Project event reader stopped ({type(exc).__name__})"
        finally:
            self.closed = True
            self.ready.set()
            async with self.changed:
                self.changed.notify_all()

    async def wait_for(
        self, predicate: Callable[[Event], bool], *, after: int = 0, max_wait_s: float = 45,
    ) -> Event:
        """Search retained events, then wait; separate observers do not consume each other's events."""
        async with asyncio.timeout(max_wait_s), self.changed:
            index = after
            while True:
                for event in self.events[index:]:
                    if predicate(event):
                        return event
                index = len(self.events)
                if self.closed:
                    raise RuntimeError(self.error or "Project socket closed")
                await self.changed.wait()


class PilotClient:
    def __init__(
        self, api: str = "http://localhost:8000", ws: str | None = None,
        timeout: float = 45, password: str | None = None,
    ) -> None:
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("Timeout must be a positive finite number")
        parts = urlsplit(api.rstrip("/"))
        if parts.scheme not in {"http", "https"} or not parts.netloc:
            raise ValueError("API URL must use http or https")
        if parts.username or parts.password or parts.query or parts.fragment:
            raise ValueError("API URL must not contain credentials, query or fragment")
        ws_url = ws or urlunsplit(("wss" if parts.scheme == "https" else "ws", parts.netloc, parts.path, "", ""))
        ws_parts = urlsplit(ws_url)
        if ws_parts.scheme not in {"ws", "wss"} or not ws_parts.netloc:
            raise ValueError("WebSocket URL must use ws or wss")
        if ws_parts.username or ws_parts.password or ws_parts.query or ws_parts.fragment:
            raise ValueError("WebSocket URL must not contain credentials, query or fragment")
        self.http = httpx.AsyncClient(base_url=api.rstrip("/"), timeout=timeout)
        self.ws_base = ws_url.rstrip("/")
        self.timeout = timeout
        self.password = password or os.environ.get("CONCORD_PILOT_PASSWORD", PILOT_PASSWORD)
        self.tokens: dict[str, str] = {}
        self.users: dict[str, dict[str, Any]] = {}
        self.project_id: int | None = None
        self.files: dict[str, dict[str, Any]] = {}

    async def __aenter__(self) -> PilotClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.http.aclose()

    def headers(self, user: str) -> dict[str, str]:
        if user not in self.tokens:
            raise ValueError(f"Log in as {user} first")
        return {"Authorization": f"Bearer {self.tokens[user]}"}

    async def login(self, users: Sequence[str] = ("alice",)) -> None:
        for user in users:
            response = await self.http.post("/auth/login", json={"username": user, "password": self.password})
            response.raise_for_status()
            body = response.json()
            self.tokens[user] = body["access_token"]
            self.users[user] = body["user"]

    async def request(self, method: str, path: str, *, user: str = "alice", **kwargs: Any) -> Any:
        response = await self.http.request(method, path, headers=self.headers(user), **kwargs)
        response.raise_for_status()
        return response.json() if response.content else None

    async def discover(self, name: str = "Pilot demo", *, owner: str = "alice") -> None:
        projects = await self.request("GET", "/projects", user=owner)
        matches = [p for p in projects if p["name"] == name and p["owner_id"] == self.users[owner]["id"]]
        if len(matches) != 1:
            raise ValueError(f"Expected one seeded project named {name!r}; found {len(matches)}")
        self.project_id = matches[0]["id"]
        files = await self.request("GET", f"/projects/{self.project_id}/files", user=owner)
        self.files = {file["path"]: file for file in files}

    def project_path(self, suffix: str = "") -> str:
        if self.project_id is None:
            raise ValueError("Discover the pilot project first")
        return f"/projects/{self.project_id}{suffix}"

    async def _socket(self, path: str, user: str) -> WebSocketClientProtocol:
        self.headers(user)
        url = f"{self.ws_base}{path}?{urlencode({'token': self.tokens[user]})}"
        try:
            return await connect(url, open_timeout=self.timeout, close_timeout=2, max_size=8 * 1024 * 1024)
        except Exception as exc:
            # Connection exception URLs can contain the authentication token.
            raise RuntimeError(f"WebSocket connection failed ({type(exc).__name__})") from None

    @asynccontextmanager
    async def project_events(self, user: str = "alice") -> AsyncIterator[EventLog]:
        path = "/ws" + self.project_path()
        socket = await self._socket(path, user)
        log = EventLog()
        reader = asyncio.create_task(log.read(socket))
        try:
            await socket.send("ping")
            await asyncio.wait_for(log.ready.wait(), timeout=self.timeout)
            if log.closed:
                raise RuntimeError(log.error or "Project socket closed before pong")
            # Membership is checked before pong; allow the Redis forwarder to subscribe too.
            await asyncio.sleep(0.25)
            yield log
        finally:
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)
            await socket.close()

    async def ensure_file(self, path: str, *, user: str = "alice") -> dict[str, Any]:
        if path not in self.files:
            self.files[path] = await self.request("POST", self.project_path("/files"), user=user, json={"path": path})
        return self.files[path]

    async def replace_text(self, file_id: int, code: str, *, user: str = "alice") -> None:
        """Edit and save an experiment file through the same CRDT protocol as the editor."""
        socket = await self._socket("/ws" + self.project_path(f"/files/{file_id}"), user)
        try:
            async with asyncio.timeout(self.timeout):
                doc = Doc()
                content = doc.get("monaco", type=Text)
                while True:
                    raw = await socket.recv()
                    if isinstance(raw, bytes):
                        doc.apply_update(raw)
                        break
                    if json.loads(raw).get("op") == "error":
                        raise RuntimeError("File socket rejected the request")
                state = doc.get_state()
                with doc.transaction():
                    if len(content):
                        del content[:]
                    content.insert(0, code)
                update = base64.b64encode(doc.get_update(state)).decode()
                await socket.send(json.dumps({"op": "update", "data": update}))
                while True:
                    raw = await socket.recv()
                    if isinstance(raw, str):
                        message = json.loads(raw)
                        if message.get("op") == "error":
                            raise RuntimeError("File edit refused")
                        if message.get("op") == "ack":
                            break
                await self.request("POST", self.project_path(f"/files/{file_id}/save"), user=user)
                saved = await self.request("GET", self.project_path(f"/files/{file_id}/content"), user=user)
                if saved.get("text") != code:
                    raise RuntimeError("File content changed while preparing the experiment")
        finally:
            await socket.close()


def latency_summary(samples_ms: Sequence[float]) -> dict[str, int | float | None]:
    values = sorted(samples_ms)
    if not values:
        return {"count": 0, "median_ms": None, "p95_ms": None, "max_ms": None}
    return {
        "count": len(values), "median_ms": statistics.median(values),
        "p95_ms": values[math.ceil(0.95 * len(values)) - 1], "max_ms": values[-1],
    }


def print_json(experiment: str, result: dict[str, Any]) -> None:
    print(json.dumps({"experiment": experiment, "reported_at": datetime.now(UTC).isoformat(), **result}, indent=2, allow_nan=False))


def argument_parser(description: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--api", default=os.environ.get("CONCORD_API_URL", "http://localhost:8000"))
    parser.add_argument("--ws", default=None, help="WebSocket base URL; derived from --api when omitted")
    parser.add_argument("--project", default="Pilot demo")
    parser.add_argument("--timeout", type=float, default=45, help="Maximum wait in seconds")
    return parser


async def inspect_pilot(args: argparse.Namespace) -> None:
    async with PilotClient(args.api, args.ws, args.timeout) as api:
        await api.login()
        await api.discover(args.project)
        async with api.project_events() as log:
            await asyncio.sleep(args.listen_seconds)
            if log.closed:
                raise RuntimeError(log.error or "Project socket closed")
            print_json("inventory", {
                "project_id": api.project_id, "files": list(api.files.values()),
                "events": [asdict(event) for event in log.events],
            })


if __name__ == "__main__":
    parser = argument_parser("Inspect seeded pilot files and record project events without starting runs")
    parser.add_argument("--listen-seconds", type=float, default=1)
    args = parser.parse_args()
    if not math.isfinite(args.listen_seconds) or args.listen_seconds < 0:
        parser.error("--listen-seconds must be finite and non-negative")
    try:
        asyncio.run(inspect_pilot(args))
    except Exception as exc:
        print_json("inventory", {"ok": False, "error": str(exc)})
        raise SystemExit(1) from None
