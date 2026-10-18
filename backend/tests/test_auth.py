import pytest

from app.core import login_limiter


class FakeLoginRedis:
    def __init__(self):
        self.values: dict[str, int] = {}
        self.ttls: dict[str, int] = {}

    async def get(self, key):
        return self.values.get(key)

    async def ttl(self, key):
        return self.ttls.get(key, -2)

    async def incr(self, key):
        self.values[key] = self.values.get(key, 0) + 1
        return self.values[key]

    async def expire(self, key, seconds):
        self.ttls[key] = seconds
        return True

    async def delete(self, key):
        existed = key in self.values
        self.values.pop(key, None)
        self.ttls.pop(key, None)
        return int(existed)


class UnavailableLoginRedis:
    async def get(self, key):
        raise ConnectionError("redis unavailable")

    async def ttl(self, key):
        raise ConnectionError("redis unavailable")

    async def incr(self, key):
        raise ConnectionError("redis unavailable")

    async def expire(self, key, seconds):
        raise ConnectionError("redis unavailable")

    async def delete(self, key):
        raise ConnectionError("redis unavailable")


@pytest.fixture
def fake_login_redis(monkeypatch):
    fake = FakeLoginRedis()
    monkeypatch.setattr(login_limiter, "_redis_client", fake)
    return fake


async def _signup(client, username):
    response = await client.post(
        "/auth/signup",
        json={
            "username": username,
            "email": f"{username}@example.com",
            "password": "supersecret1",
        },
    )
    assert response.status_code == 201, response.text


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


@pytest.mark.asyncio
async def test_login_is_throttled_after_five_failures(client, fake_login_redis):
    await _signup(client, "throttled-user")

    for _ in range(5):
        response = await client.post(
            "/auth/login", json={"username": "throttled-user", "password": "wrong"}
        )
        assert response.status_code == 401

    response = await client.post(
        "/auth/login", json={"username": "throttled-user", "password": "wrong"}
    )
    assert response.status_code == 429
    assert response.headers["Retry-After"] == "900"


@pytest.mark.asyncio
async def test_successful_login_resets_failure_counter(client, fake_login_redis):
    await _signup(client, "reset-user")
    for _ in range(3):
        response = await client.post(
            "/auth/login", json={"username": "reset-user", "password": "wrong"}
        )
        assert response.status_code == 401

    response = await client.post(
        "/auth/login", json={"username": "reset-user", "password": "supersecret1"}
    )
    assert response.status_code == 200
    assert fake_login_redis.values == {}


@pytest.mark.asyncio
async def test_unknown_username_is_throttled(client, fake_login_redis):
    for _ in range(5):
        response = await client.post(
            "/auth/login", json={"username": "missing-user", "password": "wrong"}
        )
        assert response.status_code == 401

    response = await client.post(
        "/auth/login", json={"username": "missing-user", "password": "wrong"}
    )
    assert response.status_code == 429


@pytest.mark.asyncio
async def test_correct_login_succeeds_when_redis_is_unavailable(client, monkeypatch):
    await _signup(client, "redis-down-user")
    monkeypatch.setattr(login_limiter, "_redis_client", UnavailableLoginRedis())

    response = await client.post(
        "/auth/login", json={"username": "redis-down-user", "password": "supersecret1"}
    )
    assert response.status_code == 200, response.text
