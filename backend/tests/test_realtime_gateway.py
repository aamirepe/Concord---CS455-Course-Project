"""Integration test against the real collaboration WebSocket gateway.

Everything else that touches CRDT convergence (test_concurrent_edits.py) drives
the pycrdt math directly against a simulated fanout — it never opens an actual
WebSocket, so the wire protocol (base64 JSON `update` frames in, raw binary
CRDT updates out) and room/broadcast wiring in app/realtime/gateway.py were
previously untested. This exercises the real ASGI websocket route.
"""
import asyncio
import base64
import json

import pytest
from httpx import AsyncClient
from httpx_ws import aconnect_ws
from httpx_ws.transport import ASGIWebSocketTransport
from pycrdt import Doc, Text

from app.realtime.crdt import TEXT_TYPE


@pytest.mark.asyncio
async def test_two_clients_converge_over_real_websocket(client, project):
    """Alice edits, Bob receives the broadcast update and converges to the same text."""
    a, b = project["owner"], project["other"]
    project_id, file_id = project["id"], project["file_id"]

    # Bob needs editor+ to be a meaningful second collaborator here.
    r = await client.post(
        f"/projects/{project_id}/members",
        json={"user_id": b["id"], "role": "editor"},
        headers={"Authorization": f"Bearer {a['token']}"},
    )
    assert r.status_code == 201

    from app.main import app

    ws_url = f"/ws/projects/{project_id}/files/{file_id}"

    async with AsyncClient(transport=ASGIWebSocketTransport(app=app), base_url="http://test") as ws_http:
        async with aconnect_ws(f"{ws_url}?token={a['token']}", ws_http) as alice_ws:
            # Full initial state (empty doc) sent on connect.
            await alice_ws.receive_bytes()

            async with aconnect_ws(f"{ws_url}?token={b['token']}", ws_http) as bob_ws:
                await bob_ws.receive_bytes()

                # Alice types "hello" locally and ships the resulting CRDT update.
                local_doc = Doc()
                local_doc.get(TEXT_TYPE, type=Text).insert(0, "hello")
                update = bytes(local_doc.get_update())
                await alice_ws.send_text(
                    json.dumps({"op": "update", "data": base64.b64encode(update).decode()})
                )

                # Bob receives it as a raw binary CRDT update (not Alice — no echo).
                received = await bob_ws.receive_bytes()
                assert isinstance(received, bytes | bytearray)
                assert len(received) > 0

                bob_doc = Doc()
                bob_doc.apply_update(received)
                assert str(bob_doc.get(TEXT_TYPE, type=Text)) == "hello"


@pytest.mark.asyncio
async def test_non_member_rejected_at_handshake(client, project):
    """A user who was never added to the project gets closed with a policy violation."""
    project_id, file_id = project["id"], project["file_id"]

    # A brand-new user, never added as a project member.
    r = await client.post(
        "/auth/signup",
        json={"username": "carol", "email": "carol@example.com", "password": "password123"},
    )
    assert r.status_code == 201
    token = r.json()["access_token"]

    from httpx_ws import WebSocketDisconnect

    from app.main import app

    ws_url = f"/ws/projects/{project_id}/files/{file_id}?token={token}"

    async with AsyncClient(transport=ASGIWebSocketTransport(app=app), base_url="http://test") as ws_http:
        async with aconnect_ws(ws_url, ws_http) as ws:
            msg = await ws.receive_json()
            assert msg == {"op": "error", "message": "not a project member"}
            with pytest.raises(WebSocketDisconnect):
                await ws.receive_bytes()


@pytest.mark.asyncio
async def test_removed_assist_ops_are_unknown(client, project):
    """The WS `assist_request`/`assist_apply` ops were removed: the frontend
    never used them, and `assist_apply` merged a separate Y.Doc into the room,
    appending the AI text instead of replacing the file. AI requests go through
    POST /projects/{id}/assist and edits are applied client-side via Yjs."""
    from wsproto.events import TextMessage

    a = project["owner"]
    project_id, file_id = project["id"], project["file_id"]

    from app.main import app as fastapi_app

    ws_url = f"/ws/projects/{project_id}/files/{file_id}?token={a['token']}"
    errors: list[str] = []
    async with AsyncClient(transport=ASGIWebSocketTransport(app=fastapi_app), base_url="http://test") as ws_http:
        async with aconnect_ws(ws_url, ws_http) as ws:
            await ws.receive_bytes()  # initial full state
            for op in ("assist_request", "assist_apply"):
                await ws.send_text(json.dumps({"op": op, "prompt": "x", "response": "y"}))
                for _ in range(10):
                    event = await asyncio.wait_for(ws.receive(), timeout=2)
                    if isinstance(event, TextMessage):
                        msg = json.loads(event.data)
                        if msg.get("op") == "error":
                            errors.append(msg["message"])
                            break

    assert errors == ["unknown op: assist_request", "unknown op: assist_apply"]


@pytest.mark.asyncio
async def test_ai_progress_reaches_other_members_on_project_socket(client, project, monkeypatch):
    """Bob has the project open; Alice asks the AI. Bob's project socket gets
    AI_STARTED, one AI_STEP per tool call and AI_FINISHED, with Alice's request_id."""
    from wsproto.events import TextMessage

    import app.llm.service as service_module
    from tests.agent_fakes import ScriptedProvider, answer, call, tool_turn

    a, b = project["owner"], project["other"]
    project_id = project["id"]
    ha = {"Authorization": f"Bearer {a['token']}"}
    r = await client.post(f"/projects/{project_id}/members", json={"user_id": b["id"], "role": "viewer"}, headers=ha)
    assert r.status_code == 201
    r = await client.post(
        f"/projects/{project_id}/assist/config", json={"provider": "mistral", "api_key": "sk-test-1234567890"}, headers=ha
    )
    assert r.status_code == 200
    fake = ScriptedProvider(tool_turn(call("list_files")), answer("two files"))
    monkeypatch.setattr(service_module.llm_manager, "get_provider", lambda *args, **kw: fake)

    from app.main import app as fastapi_app

    received: list[dict] = []
    async with AsyncClient(transport=ASGIWebSocketTransport(app=fastapi_app), base_url="http://test") as ws_http:
        async with aconnect_ws(f"/ws/projects/{project_id}?token={b['token']}", ws_http) as bob_ws:
            r = await client.post(
                f"/projects/{project_id}/assist", json={"prompt": "list", "request_id": "abc"}, headers=ha
            )
            assert r.status_code == 200
            while not received or received[-1]["op"] != "AI_FINISHED":
                event = await asyncio.wait_for(bob_ws.receive(), timeout=2)
                if isinstance(event, TextMessage):
                    msg = json.loads(event.data)
                    if msg["op"].startswith("AI_"):
                        received.append(msg)

    assert [m["op"] for m in received] == ["AI_STARTED", "AI_STEP", "AI_FINISHED"]
    assert all(m["request_id"] == "abc" and m["user_id"] == a["id"] for m in received)
    assert received[1]["tool"] == "list_files"


@pytest.mark.asyncio
async def test_viewer_edits_are_rejected_and_not_broadcast(client, project):
    """Viewers are read-only (user guide: "no edits"). Before this check the
    gateway applied and broadcast any member's update, whatever their role."""
    from wsproto.events import BytesMessage, TextMessage

    a, b = project["owner"], project["other"]
    project_id, file_id = project["id"], project["file_id"]
    r = await client.post(
        f"/projects/{project_id}/members",
        json={"user_id": b["id"], "role": "viewer"},
        headers={"Authorization": f"Bearer {a['token']}"},
    )
    assert r.status_code == 201

    from app.main import app as fastapi_app
    from app.realtime.manager import room_manager

    ws_url = f"/ws/projects/{project_id}/files/{file_id}"
    async with AsyncClient(transport=ASGIWebSocketTransport(app=fastapi_app), base_url="http://test") as ws_http:
        async with aconnect_ws(f"{ws_url}?token={a['token']}", ws_http) as alice_ws:
            await alice_ws.receive_bytes()
            async with aconnect_ws(f"{ws_url}?token={b['token']}", ws_http) as bob_ws:
                await bob_ws.receive_bytes()
                d = Doc()
                d.get(TEXT_TYPE, type=Text).insert(0, "vandalised")
                await bob_ws.send_text(json.dumps({"op": "update", "data": base64.b64encode(bytes(d.get_update())).decode()}))

                error = None
                for _ in range(10):
                    event = await asyncio.wait_for(bob_ws.receive(), timeout=2)
                    if isinstance(event, TextMessage) and json.loads(event.data).get("op") == "error":
                        error = json.loads(event.data)["message"]
                        break
                assert error == "read-only: viewers can't edit"

                # Alice got no update, and the server's copy is unchanged
                with pytest.raises(TimeoutError):
                    while True:
                        event = await asyncio.wait_for(alice_ws.receive(), timeout=0.3)
                        assert not isinstance(event, BytesMessage)
                room = room_manager.get_room(project_id, file_id)
                assert str(room.doc.get(TEXT_TYPE, type=Text)) == ""
