"""Many WebSocket clients editing one file through the real gateway converge.

test_concurrent_edits.py checks the CRDT math with 10 in-process replicas and
test_realtime_gateway.py checks the wire protocol with two clients. This opens
10 and 25 real WebSocket connections to the ASGI gateway, has every client edit
the same file at once, and checks that all of them end with the same text as
the server and that no edit was lost (FR-08, FR-14, NFR-D3).
"""
import asyncio
import base64
import json
import random
import string

import pytest
from httpx import AsyncClient
from httpx_ws import aconnect_ws
from httpx_ws.transport import ASGIWebSocketTransport
from pycrdt import Doc, Text
from wsproto.events import BytesMessage

from app.realtime.crdt import TEXT_TYPE

EDITS_PER_CLIENT = 4
# ASCII only: pycrdt counts text positions in UTF-8 bytes, so a multi-byte character
# would let a random insert position land inside it.
ALPHABET = string.ascii_letters + string.digits
SETTLE_SECONDS = 20


async def _add_editors(client: AsyncClient, project: dict, count: int) -> list[dict]:
    """The project owner plus ``count - 1`` freshly signed-up editors."""
    owner = project["owner"]
    users = [owner]
    for i in range(count - 1):
        r = await client.post(
            "/auth/signup",
            json={"username": f"editor{i}", "email": f"editor{i}@example.com", "password": "password123"},
        )
        assert r.status_code == 201
        body = r.json()
        users.append({"id": body["user"]["id"], "token": body["access_token"]})
        r = await client.post(
            f"/projects/{project['id']}/members",
            json={"user_id": body["user"]["id"], "role": "editor"},
            headers={"Authorization": f"Bearer {owner['token']}"},
        )
        assert r.status_code == 201
    return users


async def _read_updates(ws, doc: Doc) -> None:
    """Apply every binary CRDT update the server sends to this client's copy."""
    while True:
        event = await ws.receive()
        if isinstance(event, BytesMessage):
            doc.apply_update(bytes(event.data))


async def _edit(ws, doc: Doc, client_no: int, rng: random.Random) -> list[str]:
    """Insert one character per edit at a random position, shipping each as its own update.

    A single character can't be split by someone else's concurrent insert, so
    afterwards the text must hold exactly the characters that were sent."""
    text = doc.get(TEXT_TYPE, type=Text)
    tokens = []
    for n in range(EDITS_PER_CLIENT):
        token = ALPHABET[(client_no * EDITS_PER_CLIENT + n) % len(ALPHABET)]
        before = doc.get_state()
        text.insert(rng.randint(0, len(text)), token)
        update = bytes(doc.get_update(before))
        await ws.send_text(json.dumps({"op": "update", "data": base64.b64encode(update).decode()}))
        tokens.append(token)
        await asyncio.sleep(0)  # let the other clients' edits interleave
    return tokens


@pytest.mark.asyncio
@pytest.mark.parametrize("clients", [10, 25])
async def test_clients_editing_one_file_converge_through_the_gateway(client, project, clients):
    users = await _add_editors(client, project, clients)
    project_id, file_id = project["id"], project["file_id"]

    from app.main import app
    from app.realtime.manager import room_manager

    ws_url = f"/ws/projects/{project_id}/files/{file_id}"
    docs = [Doc() for _ in users]
    rng = random.Random(clients)  # noqa: S311 -- deterministic test data, not cryptographic
    all_connected = asyncio.Barrier(clients)
    all_sent = asyncio.Barrier(clients + 1)  # the clients and the checker
    settled = asyncio.Event()
    sent: list[list[str]] = [[] for _ in users]

    async def run_client(i: int) -> None:
        # Each client owns its socket from connect to close, in its own task.
        async with AsyncClient(transport=ASGIWebSocketTransport(app=app), base_url="http://test") as ws_http:
            async with aconnect_ws(f"{ws_url}?token={users[i]['token']}", ws_http) as ws:
                # The first frame is the (empty) document; nobody types before everyone has it.
                docs[i].apply_update(bytes(await ws.receive_bytes()))
                await all_connected.wait()
                reader = asyncio.create_task(_read_updates(ws, docs[i]))
                try:
                    sent[i] = await _edit(ws, docs[i], i, rng)
                    await all_sent.wait()
                    await settled.wait()
                finally:
                    reader.cancel()
                    await asyncio.gather(reader, return_exceptions=True)

    async def check() -> None:
        await all_sent.wait()
        room = room_manager.get_room(project_id, file_id)
        expected = sorted(token for tokens in sent for token in tokens)
        assert len(expected) == clients * EDITS_PER_CLIENT

        def lagging() -> list[str]:
            server_text = str(room.doc.get(TEXT_TYPE, type=Text))
            problems = [] if sorted(server_text) == expected else ["the server lost or duplicated an edit"]
            for i, doc in enumerate(docs):
                if str(doc.get(TEXT_TYPE, type=Text)) != server_text:
                    problems.append(f"client {i} differs from the server")
            return problems

        try:
            deadline = asyncio.get_running_loop().time() + SETTLE_SECONDS
            while lagging() and asyncio.get_running_loop().time() < deadline:  # noqa: ASYNC110 -- polls plain state
                await asyncio.sleep(0.05)
            assert not lagging()
        finally:
            settled.set()

    async with asyncio.timeout(SETTLE_SECONDS * 3):
        await asyncio.gather(check(), *(run_client(i) for i in range(clients)))
