"""Hostile-client checks for the public REST and WebSocket interfaces."""
from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from httpx_ws import WebSocketDisconnect, aconnect_ws
from httpx_ws.transport import ASGIWebSocketTransport
from jose import jwt

from app.core.security import create_access_token, hash_password
from app.main import app
from app.models import User


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
