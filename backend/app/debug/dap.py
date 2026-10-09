"""DAP (Debug Adapter Protocol) client wrapper.

Translates the protocol debugpy speaks into the small surface we need (set
breakpoints, continue, step, get variables / call stack). It talks either TCP
(``connect``) or any pair of asyncio streams (``attach_streams``): the worker
runs debugpy's adapter inside the sandbox and speaks DAP over the container's
stdin/stdout, so the sandbox keeps having no network.

Reference: https://microsoft.github.io/debug-adapter-protocol/
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

log = logging.getLogger(__name__)


class DapProtocolError(RuntimeError):
    pass


class DapClient:
    """Async TCP client for the Debug Adapter Protocol.

    debugpy, when started with `--wait-for-client`, accepts a single DAP
    client on the configured port. Multiple clients are not supported by
    debugpy itself, so this is single-driver by design.
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 5678) -> None:
        self.host = host
        self.port = port
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._seq = 1
        self._pending: dict[int, asyncio.Future[dict]] = {}
        self._event_handlers: dict[str, list] = {}
        self._connected = asyncio.Event()
        self._closed = False
        self._read_task: asyncio.Task[None] | None = None

    async def connect(self, timeout: float = 30.0) -> None:  # noqa: ASYNC109 -- polling deadline, not asyncio.timeout(); callers pass an explicit budget
        deadline = time.monotonic() + timeout
        while True:
            try:
                self._reader, self._writer = await asyncio.open_connection(self.host, self.port)
                break
            except (ConnectionRefusedError, OSError):
                if time.monotonic() > deadline:
                    raise
                await asyncio.sleep(0.2)
        self._connected.set()
        self._read_task = asyncio.create_task(self._read_loop())

    def attach_streams(self, reader: asyncio.StreamReader, writer: Any) -> None:
        """Use already-open streams (``writer`` needs ``write`` and ``drain``)."""
        self._reader, self._writer = reader, writer
        self._connected.set()
        self._read_task = asyncio.create_task(self._read_loop())

    @property
    def done(self) -> bool:
        """The adapter hung up (or we closed)."""
        return self._closed or (self._read_task is not None and self._read_task.done())

    async def close(self) -> None:
        self._closed = True
        self._fail_pending(ConnectionError("DAP connection closed"))
        if self._writer is not None:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:
                log.debug("error closing DAP socket", exc_info=True)

    def on(self, event: str, handler) -> None:
        self._event_handlers.setdefault(event, []).append(handler)

    async def _read_loop(self) -> None:
        assert self._reader is not None
        try:
            while not self._closed:
                msg = await self._read_message()
                if msg is None:
                    break
                if msg.get("type") == "response":
                    req_seq = msg.get("request_seq")
                    fut = self._pending.pop(req_seq, None) if isinstance(req_seq, int) else None
                    if fut and not fut.done():
                        fut.set_result(msg)
                elif msg.get("type") == "event":
                    event = msg.get("event", "")
                    for handler in self._event_handlers.get(event, []):
                        try:
                            handler(msg)
                        except Exception:
                            log.exception("event handler errored for %s", event)
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        except Exception:
            log.exception("DAP read loop crashed")
        # Nobody will answer the requests still waiting.
        self._fail_pending(ConnectionError("debug adapter disconnected"))
        for handler in self._event_handlers.get("_closed", []):
            try:
                handler({})
            except Exception:
                log.exception("close handler errored")

    def _fail_pending(self, exc: BaseException) -> None:
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(exc)
                fut.exception()  # a request nobody awaits any more (e.g. launch) shouldn't log "never retrieved"
        self._pending.clear()

    async def _read_message(self) -> dict | None:
        """Read one Content-Length framed DAP message."""
        assert self._reader is not None
        headers: dict[str, str] = {}
        while True:
            raw_line = await self._reader.readline()
            if not raw_line:
                return None
            text_line = raw_line.decode("utf-8").rstrip("\r\n")
            if not text_line:
                break
            if ":" in text_line:
                k, v = text_line.split(":", 1)
                headers[k.strip().lower()] = v.strip()

        length = int(headers.get("content-length", "0"))
        if length <= 0:
            return None
        body = await self._reader.readexactly(length)
        return json.loads(body)

    async def _send(self, payload: dict) -> None:
        assert self._writer is not None
        body = json.dumps(payload).encode("utf-8")
        header = f"Content-Length: {len(body)}\r\n\r\n".encode()
        self._writer.write(header + body)
        await self._writer.drain()

    async def start_request(self, command: str, arguments: dict | None = None) -> asyncio.Future[dict]:
        """Send a request; return a future for its response without waiting for it.

        debugpy answers ``launch`` only after ``configurationDone``, which we
        may send only after the ``initialized`` event, so the handshake can't
        await each request in turn.
        """
        await self._connected.wait()
        seq = self._seq
        self._seq += 1
        fut: asyncio.Future[dict] = asyncio.get_running_loop().create_future()
        self._pending[seq] = fut
        await self._send({"seq": seq, "type": "request", "command": command, "arguments": arguments or {}})
        return fut

    @staticmethod
    def check(command: str, response: dict) -> dict:
        if not response.get("success", False):
            raise DapProtocolError(f"DAP {command} failed: {response.get('message')}")
        return response

    async def _request(self, command: str, arguments: dict | None = None) -> dict:
        await self._connected.wait()
        seq = self._seq
        self._seq += 1
        payload = {
            "seq": seq,
            "type": "request",
            "command": command,
            "arguments": arguments or {},
        }
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[dict] = loop.create_future()
        self._pending[seq] = fut
        await self._send(payload)
        response = await asyncio.wait_for(fut, timeout=60)
        if not response.get("success", False):
            raise DapProtocolError(f"DAP {command} failed: {response.get('message')}")
        return response

    # --- high-level operations our backend exposes ---

    async def initialize(self) -> None:
        await self._request("initialize", {
            "clientID": "concord", "adapterID": "debugpy", "pathFormat": "path",
            "linesStartAt1": True, "columnsStartAt1": True,
        })

    async def set_breakpoints(self, file_path: str, lines: list[int]) -> None:
        bps = [{"line": line} for line in lines]
        await self._request("setBreakpoints", {"source": {"path": file_path}, "breakpoints": bps})

    async def configuration_done(self) -> None:
        await self._request("configurationDone")

    async def continue_(self, thread_id: int) -> None:
        await self._request("continue", {"threadId": thread_id})

    async def next(self, thread_id: int) -> None:
        await self._request("next", {"threadId": thread_id})

    async def step_in(self, thread_id: int) -> None:
        await self._request("stepIn", {"threadId": thread_id})

    async def step_out(self, thread_id: int) -> None:
        await self._request("stepOut", {"threadId": thread_id})

    async def pause(self, thread_id: int) -> None:
        await self._request("pause", {"threadId": thread_id})

    async def disconnect(self, terminate: bool = True) -> None:
        await self._request("disconnect", {"terminateDebuggee": terminate})

    async def stack_trace(self, thread_id: int) -> list[dict]:
        resp = await self._request("stackTrace", {"threadId": thread_id, "startFrame": 0, "levels": 20})
        return resp.get("body", {}).get("stackFrames", [])

    async def scopes(self, frame_id: int) -> list[dict]:
        resp = await self._request("scopes", {"frameId": frame_id})
        return resp.get("body", {}).get("scopes", [])

    async def variables(self, variables_reference: int) -> list[dict]:
        resp = await self._request("variables", {"variablesReference": variables_reference})
        return resp.get("body", {}).get("variables", [])

    async def evaluate(self, expression: str, frame_id: int | None = None) -> Any:
        args: dict[str, Any] = {"expression": expression}
        if frame_id is not None:
            args["frameId"] = frame_id
        resp = await self._request("evaluate", args)
        return resp.get("body", {})
