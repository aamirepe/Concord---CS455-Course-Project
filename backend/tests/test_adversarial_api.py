"""Hostile-client checks for the public REST and WebSocket interfaces."""
from __future__ import annotations

import asyncio
import base64
import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.routing import APIRoute
from httpx import AsyncClient
from httpx_ws import WebSocketDisconnect, aconnect_ws
from httpx_ws.transport import ASGIWebSocketTransport
from jose import jwt
from pycrdt import Doc, Text
from wsproto.events import TextMessage

from app.core import login_limiter
from app.core.security import create_access_token, hash_password
from app.main import app
from app.models import User
from app.realtime.crdt import TEXT_TYPE


class _LoginRedis:
    def __init__(self) -> None:
        self.values: dict[str, int] = {}
        self.ttls: dict[str, int] = {}

    async def get(self, key: str) -> int | None:
        return self.values.get(key)

    async def ttl(self, key: str) -> int:
        return self.ttls.get(key, -2)

    async def incr(self, key: str) -> int:
        self.values[key] = self.values.get(key, 0) + 1
        return self.values[key]

    async def expire(self, key: str, seconds: int) -> bool:
        self.ttls[key] = seconds
        return True

    async def delete(self, key: str) -> int:
        existed = key in self.values
        self.values.pop(key, None)
        self.ttls.pop(key, None)
        return int(existed)


def _base64url(value: dict) -> str:
    raw = json.dumps(value, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _unsigned_token(user_id: int) -> str:
    now = datetime.now(UTC)
    header = _base64url({"alg": "none", "typ": "JWT"})
    payload = _base64url(
        {
            "sub": str(user_id),
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=5)).timestamp()),
        }
    )
    return f"{header}.{payload}."


async def _assert_websocket_token_rejected(
    url: str, http: AsyncClient, *, messages: set[str] | None = None
) -> None:
    expected_messages = messages or {"invalid token"}
    async with aconnect_ws(url, http) as websocket:
        error = await websocket.receive_json()
        assert error["op"] == "error"
        assert error["message"] in expected_messages
        with pytest.raises(WebSocketDisconnect) as closed:
            await websocket.receive()
        assert closed.value.code == 1008


async def _assert_token_rejected_everywhere(
    client,
    project: dict,
    token: str | None,
    *,
    rest_statuses: set[int] | None = None,
    websocket_messages: set[str] | None = None,
) -> None:
    headers = {"Authorization": f"Bearer {token}"} if token is not None else {}
    response = await client.get("/projects", headers=headers)
    assert response.status_code in (rest_statuses or {401})

    query = f"?token={token}" if token is not None else ""
    async with AsyncClient(
        transport=ASGIWebSocketTransport(app=app), base_url="http://test"
    ) as websocket_http:
        await _assert_websocket_token_rejected(
            f"/ws/projects/{project['id']}{query}",
            websocket_http,
            messages=websocket_messages,
        )
        await _assert_websocket_token_rejected(
            f"/ws/projects/{project['id']}/files/{project['file_id']}{query}",
            websocket_http,
            messages=websocket_messages,
        )


@pytest.mark.asyncio
async def test_token_signed_with_alg_none_is_rejected(client, project):
    await _assert_token_rejected_everywhere(
        client, project, _unsigned_token(project["owner"]["id"])
    )


@pytest.mark.asyncio
async def test_token_signed_with_different_key_is_rejected(client, project):
    now = datetime.now(UTC)
    payload = {
        "sub": str(project["owner"]["id"]),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=5)).timestamp()),
    }
    token = jwt.encode(payload, "different-secret-key", algorithm="HS256")
    await _assert_token_rejected_everywhere(client, project, token)


@pytest.mark.asyncio
async def test_expired_token_is_rejected(client, project):
    token = create_access_token(
        project["owner"]["id"],
        extra={"exp": int((datetime.now(UTC) - timedelta(seconds=1)).timestamp())},
    )
    await _assert_token_rejected_everywhere(client, project, token)


@pytest.mark.asyncio
async def test_token_for_deleted_user_is_rejected(client, project, db):
    deleted = User(
        username="deleted-token-user",
        email="deleted-token-user@example.com",
        password_hash=hash_password("password123"),
    )
    db.add(deleted)
    await db.commit()
    await db.refresh(deleted)
    token = create_access_token(deleted.id)
    await db.delete(deleted)
    await db.commit()

    await _assert_token_rejected_everywhere(
        client,
        project,
        token,
        websocket_messages={"invalid token", "not a project member"},
    )


@pytest.mark.asyncio
async def test_malformed_token_is_rejected(client, project):
    await _assert_token_rejected_everywhere(client, project, "not-a-jwt")


@pytest.mark.asyncio
async def test_missing_token_is_rejected(client, project):
    await _assert_token_rejected_everywhere(
        client, project, None, rest_statuses={401, 403}
    )


def _project_routes() -> list[tuple[str, str, str]]:
    routes: list[tuple[str, str, str]] = []
    for route in app.routes:
        if not isinstance(route, APIRoute) or "{project_id}" not in route.path:
            continue
        for method in route.methods:
            if method not in {"HEAD", "OPTIONS"}:
                routes.append((method, route.path, route.name))
    return sorted(routes)


def _route_request(
    method: str, path: str, name: str, project: dict
) -> tuple[str, dict, dict | None]:
    identifiers = {
        "project_id": project["id"],
        "request_id": 999_999,
        "user_id": project["owner"]["id"],
        "folder_id": project.get("folder_id", 999_999),
        "file_id": project["file_id"],
        "number": 1,
        "snapshot_id": 999_999,
        "execution_id": 999_999,
    }
    payloads: dict[str, object] = {
        "approve_join_request": {"role": "viewer"},
        "add_project_member": {
            "user_id": project["owner"]["id"],
            "role": "viewer",
        },
        "update_project_member_role": {"role": "viewer"},
        "create_folder": {"name": "adversarial-folder"},
        "update_folder": {"name": "adversarial-folder"},
        "create_file": {"path": "adversarial.py"},
        "update_file": {"path": "adversarial.py"},
        "create_snapshot": {"label": "adversarial snapshot"},
        "run_code": {"file_id": project["file_id"]},
        "set_file_breakpoints": {"lines": [1]},
        "upsert_breakpoints": [],
        "debug_command": {"command": "continue"},
        "configure_assistant": {
            "provider": "mistral",
            "api_key": "test-api-key",
        },
        "update_assistant_config": {"is_active": False},
        "request_assistant": {"prompt": "read the project"},
    }
    params: dict[str, object] = {}
    if name == "start_debug":
        params["file_id"] = project["file_id"]
    if name == "list_breakpoints":
        params["file_id"] = project["file_id"]
    if name == "search_project":
        params["q"] = "needle"
    body = payloads.get(name) if method not in {"GET", "DELETE"} else None
    return path.format(**identifiers), params, body


async def _call_project_route(
    client,
    route: tuple[str, str, str],
    project: dict,
    token: str,
):
    method, path, name = route
    url, params, body = _route_request(method, path, name, project)
    return await client.request(
        method,
        url,
        params=params,
        json=body,
        headers={"Authorization": f"Bearer {token}"},
    )


@pytest.mark.asyncio
async def test_non_member_cannot_access_protected_project_routes(client, project):
    tested: set[str] = set()
    for route in _project_routes():
        response = await _call_project_route(
            client, route, project, project["other"]["token"]
        )
        tested.add(route[2])
        if route[2] == "request_to_join":
            assert response.status_code == 201
        else:
            assert response.status_code in {403, 404}, (
                route,
                response.status_code,
                response.text,
            )

    assert tested == {
        route.name
        for route in app.routes
        if isinstance(route, APIRoute) and "{project_id}" in route.path
    }


@pytest.mark.asyncio
async def test_viewer_cannot_call_project_write_routes(client, project):
    owner, viewer = project["owner"], project["other"]
    owner_headers = {"Authorization": f"Bearer {owner['token']}"}
    response = await client.post(
        f"/projects/{project['id']}/folders",
        json={"name": "existing-folder"},
        headers=owner_headers,
    )
    assert response.status_code == 201
    route_project = {**project, "folder_id": response.json()["id"]}
    response = await client.post(
        f"/projects/{project['id']}/members",
        json={"user_id": viewer["id"], "role": "viewer"},
        headers=owner_headers,
    )
    assert response.status_code == 201

    intentionally_available = {
        "join_project",
        "request_to_join",
        "request_assistant",
    }
    write_routes = [
        route
        for route in _project_routes()
        if route[0] != "GET" and route[2] not in intentionally_available
    ]
    for route in write_routes:
        response = await _call_project_route(
            client, route, route_project, viewer["token"]
        )
        assert response.status_code == 403, (
            route,
            response.status_code,
            response.text,
        )


@pytest.mark.asyncio
async def test_viewer_websocket_edit_is_rejected(client, project):
    owner, viewer = project["owner"], project["other"]
    response = await client.post(
        f"/projects/{project['id']}/members",
        json={"user_id": viewer["id"], "role": "viewer"},
        headers={"Authorization": f"Bearer {owner['token']}"},
    )
    assert response.status_code == 201

    url = f"/ws/projects/{project['id']}/files/{project['file_id']}"
    async with AsyncClient(
        transport=ASGIWebSocketTransport(app=app), base_url="http://test"
    ) as websocket_http:
        async with aconnect_ws(
            f"{url}?token={viewer['token']}", websocket_http
        ) as websocket:
            await websocket.receive_bytes()
            document = Doc()
            document.get(TEXT_TYPE, type=Text).insert(0, "unauthorised edit")
            update = base64.b64encode(bytes(document.get_update())).decode()
            await websocket.send_text(json.dumps({"op": "update", "data": update}))

            for _ in range(10):
                event = await asyncio.wait_for(websocket.receive(), timeout=2)
                if isinstance(event, TextMessage):
                    message = json.loads(event.data)
                    if message.get("op") == "error":
                        assert message["message"] == "read-only: viewers can't edit"
                        break
            else:
                pytest.fail("viewer edit was not refused")


@pytest.mark.asyncio
async def test_websocket_cannot_open_file_from_another_project(client, project):
    owner = project["owner"]
    headers = {"Authorization": f"Bearer {owner['token']}"}
    response = await client.post(
        "/projects", json={"name": "foreign project"}, headers=headers
    )
    assert response.status_code == 201
    foreign_project_id = response.json()["id"]
    response = await client.get(
        f"/projects/{foreign_project_id}/files", headers=headers
    )
    assert response.status_code == 200
    foreign_file_id = response.json()[0]["id"]

    url = (
        f"/ws/projects/{project['id']}/files/{foreign_file_id}"
        f"?token={owner['token']}"
    )
    async with AsyncClient(
        transport=ASGIWebSocketTransport(app=app), base_url="http://test"
    ) as websocket_http:
        async with aconnect_ws(url, websocket_http) as websocket:
            assert await websocket.receive_json() == {
                "op": "error",
                "message": "file not found",
            }
            with pytest.raises(WebSocketDisconnect) as closed:
                await websocket.receive()
            assert closed.value.code == 1008


@pytest.mark.asyncio
async def test_traversal_paths_are_rejected_on_file_and_folder_writes(
    client, project
):
    headers = {"Authorization": f"Bearer {project['owner']['token']}"}
    attacks = [
        ("POST", f"/projects/{project['id']}/files", {"path": "../secret.py"}),
        (
            "PATCH",
            f"/projects/{project['id']}/files/{project['file_id']}",
            {"path": "/" + "absolute.py"},
        ),
        ("POST", f"/projects/{project['id']}/folders", {"name": ".."}),
    ]
    for method, url, body in attacks:
        response = await client.request(method, url, json=body, headers=headers)
        assert response.status_code == 422, (url, response.text)


@pytest.mark.asyncio
async def test_standard_input_larger_than_64_kib_is_rejected(client, project):
    response = await client.post(
        f"/projects/{project['id']}/executions",
        json={"file_id": project["file_id"], "stdin": "x" * (64 * 1024 + 1)},
        headers={"Authorization": f"Bearer {project['owner']['token']}"},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_oversized_file_content_does_not_crash_websocket(client, project):
    owner = project["owner"]
    url = (
        f"/ws/projects/{project['id']}/files/{project['file_id']}"
        f"?token={owner['token']}"
    )
    async with AsyncClient(
        transport=ASGIWebSocketTransport(app=app), base_url="http://test"
    ) as websocket_http:
        async with aconnect_ws(url, websocket_http) as websocket:
            await websocket.receive_bytes()
            document = Doc()
            document.get(TEXT_TYPE, type=Text).insert(0, "x" * (1024 * 1024 + 1))
            oversized = base64.b64encode(bytes(document.get_update())).decode()
            await websocket.send_text(
                json.dumps({"op": "update", "data": oversized})
            )
            for _ in range(10):
                event = await asyncio.wait_for(websocket.receive(), timeout=2)
                if isinstance(event, TextMessage):
                    message = json.loads(event.data)
                    if message.get("op") == "ack":
                        break
            else:
                pytest.fail("oversized file update was not acknowledged")


@pytest.mark.asyncio
async def test_sixth_wrong_password_is_rate_limited(client, monkeypatch):
    fake_redis = _LoginRedis()
    monkeypatch.setattr(login_limiter, "_redis_client", fake_redis)
    response = await client.post(
        "/auth/signup",
        json={
            "username": "adversarial-login",
            "email": "adversarial-login@example.com",
            "password": "supersecret1",
        },
    )
    assert response.status_code == 201

    for _ in range(5):
        response = await client.post(
            "/auth/login",
            json={"username": "adversarial-login", "password": "wrong"},
        )
        assert response.status_code == 401

    response = await client.post(
        "/auth/login",
        json={"username": "adversarial-login", "password": "wrong"},
    )
    assert response.status_code == 429
    assert response.headers["Retry-After"] == "900"
