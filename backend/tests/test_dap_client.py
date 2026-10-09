"""Unit tests for DapClient against a real local asyncio TCP loopback server
acting as a fake debugpy — exercises the real Content-Length framing and
_read_loop dispatch code, not just the high-level wrapper methods.

These are also the regression tests for the dispatch-guard bug fixed in this
phase: _read_loop used to have `if "msg" not in msg: continue`, which is true
for every real DAP message (DAP has no top-level "msg" key) — so no response
or event was ever actually processed, and every _request() call just hung
until its 60s timeout. Before the fix, every test in this file would time out.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from app.debug.dap import DapClient, DapProtocolError


async def _start_fake_server(responder):
    """responder(request: dict) -> dict | None. Returning None sends nothing
    (used when the test wants to push an event out-of-band instead)."""

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while True:
                headers: dict[str, str] = {}
                while True:
                    line = await reader.readline()
                    if not line:
                        return
                    text = line.decode("utf-8").rstrip("\r\n")
                    if not text:
                        break
                    key, _, value = text.partition(":")
                    headers[key.strip().lower()] = value.strip()

                length = int(headers.get("content-length", "0"))
                body = await reader.readexactly(length)
                request = json.loads(body)

                response = responder(request, writer)
                if response is not None:
                    await _send_frame(writer, response)
        except (asyncio.IncompleteReadError, ConnectionResetError):
            pass
        finally:
            # Python 3.12's Server.wait_closed() waits for every connection
            # the server accepted, so the handler has to close its side.
            writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    return server, port


async def _send_frame(writer: asyncio.StreamWriter, payload: dict) -> None:
    body = json.dumps(payload).encode("utf-8")
    writer.write(f"Content-Length: {len(body)}\r\n\r\n".encode() + body)
    await writer.drain()


@pytest.mark.asyncio
async def test_request_response_round_trip():
    def responder(request: dict, writer) -> dict:
        return {
            "seq": 99,
            "type": "response",
            "request_seq": request["seq"],
            "success": True,
            "command": request["command"],
            "body": {"echo": request["arguments"]},
        }

    server, port = await _start_fake_server(responder)
    client = DapClient("127.0.0.1", port)
    try:
        await client.connect()
        response = await client._request("initialize", {"clientID": "concord"})
        assert response["body"] == {"echo": {"clientID": "concord"}}
    finally:
        await client.close()
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_unsuccessful_response_raises_dap_protocol_error():
    def responder(request: dict, writer) -> dict:
        return {
            "seq": 1,
            "type": "response",
            "request_seq": request["seq"],
            "success": False,
            "command": request["command"],
            "message": "breakpoint out of range",
        }

    server, port = await _start_fake_server(responder)
    client = DapClient("127.0.0.1", port)
    try:
        await client.connect()
        with pytest.raises(DapProtocolError, match="breakpoint out of range"):
            await client._request("setBreakpoints", {})
    finally:
        await client.close()
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_high_level_stack_trace_unwraps_body():
    def responder(request: dict, writer) -> dict:
        assert request["command"] == "stackTrace"
        return {
            "seq": 1,
            "type": "response",
            "request_seq": request["seq"],
            "success": True,
            "command": "stackTrace",
            "body": {"stackFrames": [{"id": 1, "name": "main", "line": 5}]},
        }

    server, port = await _start_fake_server(responder)
    client = DapClient("127.0.0.1", port)
    try:
        await client.connect()
        frames = await client.stack_trace(thread_id=1)
        assert frames == [{"id": 1, "name": "main", "line": 5}]
    finally:
        await client.close()
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_event_message_dispatches_to_registered_handler():
    async def handle_and_push_event(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        # Push an unsolicited "stopped" event as soon as the client connects —
        # exactly like debugpy does when a breakpoint is hit.
        await _send_frame(
            writer,
            {"seq": 1, "type": "event", "event": "stopped", "body": {"threadId": 7}},
        )
        # Hold the connection until the client hangs up, then close our side
        # so server.wait_closed() can return on Python 3.12.
        await reader.read()
        writer.close()

    server = await asyncio.start_server(handle_and_push_event, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]

    client = DapClient("127.0.0.1", port)
    received: list[dict] = []
    client.on("stopped", lambda msg: received.append(msg))
    try:
        await client.connect()
        # Give the read loop a moment to process the pushed event.
        for _ in range(20):
            if received:
                break
            await asyncio.sleep(0.05)
        assert received == [{"seq": 1, "type": "event", "event": "stopped", "body": {"threadId": 7}}]
    finally:
        await client.close()
        server.close()
        await server.wait_closed()
