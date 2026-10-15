"""The collaboration socket only opens files of the project in its URL."""
from __future__ import annotations

import json

import pytest
from httpx import AsyncClient
from httpx_ws import WebSocketDisconnect, aconnect_ws
from httpx_ws.transport import ASGIWebSocketTransport

from app.realtime.manager import room_manager


@pytest.mark.asyncio
async def test_a_file_of_another_project_is_refused(client, project):
    a, b = project["owner"], project["other"]
    from app.main import app

    # Bob owns a second project; its file must not be reachable through Alice's.
    r = await client.post("/projects", json={"name": "p2"}, headers={"Authorization": f"Bearer {b['token']}"})
    assert r.status_code == 201
    other_project = r.json()["id"]
    r = await client.get(f"/projects/{other_project}/files", headers={"Authorization": f"Bearer {b['token']}"})
    other_file = r.json()[0]["id"]

    url = f"/ws/projects/{project['id']}/files/{other_file}?token={a['token']}"
    async with AsyncClient(transport=ASGIWebSocketTransport(app=app), base_url="http://test") as http:
        async with aconnect_ws(url, http) as ws:
            first = json.loads(await ws.receive_text())
            assert first["op"] == "error"
            with pytest.raises(WebSocketDisconnect) as closed:
                await ws.receive()
            assert closed.value.code == 1008

    # Nothing was loaded or created for either project's copy of that file.
    assert room_manager.get_room(project["id"], other_file) is None
    assert room_manager.get_room(other_project, other_file) is None
