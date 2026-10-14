import pytest


@pytest.mark.asyncio
async def test_signup_login_flow(client):
    r = await client.post(
        "/auth/signup",
        json={"username": "alice", "email": "alice@example.com", "password": "supersecret1"},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert "access_token" in body
    assert body["user"]["username"] == "alice"

    r = await client.post(
        "/auth/login", json={"username": "alice", "password": "supersecret1"}
    )
    assert r.status_code == 200, r.text
    assert "access_token" in r.json()


@pytest.mark.asyncio
async def test_login_wrong_password_rejected(client):
    await client.post(
        "/auth/signup",
        json={"username": "carol", "email": "carol@example.com", "password": "supersecret1"},
    )
    r = await client.post(
        "/auth/login", json={"username": "carol", "password": "WRONG"}
    )
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_duplicate_signup_rejected(client):
    await client.post(
        "/auth/signup",
        json={"username": "dave", "email": "dave@example.com", "password": "supersecret1"},
    )
    r = await client.post(
        "/auth/signup",
        json={"username": "dave", "email": "dave2@example.com", "password": "supersecret1"},
    )
    assert r.status_code == 409
