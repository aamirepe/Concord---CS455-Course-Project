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


@pytest.mark.asyncio
async def test_signup_rejects_username_and_email_owned_by_different_users(client):
    for username, email in (
        ("alice-existing", "alice-existing@example.com"),
        ("bob-existing", "bob-existing@example.com"),
    ):
        response = await client.post(
            "/auth/signup",
            json={"username": username, "email": email, "password": "supersecret1"},
        )
        assert response.status_code == 201, response.text

    response = await client.post(
        "/auth/signup",
        json={
            "username": "alice-existing",
            "email": "bob-existing@example.com",
            "password": "supersecret1",
        },
    )

    assert response.status_code == 409
