"""Program input (stdin) for runs, and the EXECUTION_QUEUED event."""
from __future__ import annotations

import json
import os

import pytest

from app.realtime.manager import room_manager
from app.services.sandbox import MAX_STDIN_BYTES, STDIN_FILE, write_stdin


@pytest.fixture
def broadcasts(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    sent: list[dict] = []
    monkeypatch.setattr(room_manager, "broadcast_to_project", lambda pid, payload, exclude=None: sent.append(json.loads(payload)))
    return sent


@pytest.mark.asyncio
async def test_run_passes_stdin_to_the_worker_and_announces_who_ran_it(client, project, no_op_enqueue, broadcasts):
    owner = project["owner"]
    r = await client.post(
        f"/projects/{project['id']}/executions",
        json={"file_id": project["file_id"], "stdin": "3\n4\n"},
        headers={"Authorization": f"Bearer {owner['token']}"},
    )
    assert r.status_code == 202, r.text
    assert no_op_enqueue[-1]["stdin"] == "3\n4\n"
    assert broadcasts == [{
        "op": "EXECUTION_QUEUED", "execution_id": r.json()["id"], "file_id": project["file_id"],
        "path": "main.py", "user_id": owner["id"], "username": owner["username"], "debug": False,
    }]


@pytest.mark.asyncio
async def test_stdin_is_optional_and_limited(client, project, no_op_enqueue, broadcasts):
    h = {"Authorization": f"Bearer {project['owner']['token']}"}
    url = f"/projects/{project['id']}/executions"
    assert (await client.post(url, json={"file_id": project["file_id"]}, headers=h)).status_code == 202
    assert no_op_enqueue[-1]["stdin"] is None
    too_big = {"file_id": project["file_id"], "stdin": "x" * (64 * 1024 + 1)}
    assert (await client.post(url, json=too_big, headers=h)).status_code == 422


def test_write_stdin_writes_an_empty_file_without_input(tmp_path):
    write_stdin(str(tmp_path), None)
    assert (tmp_path / STDIN_FILE).read_text() == ""
    write_stdin(str(tmp_path), "y" * (MAX_STDIN_BYTES + 10))
    assert os.path.getsize(tmp_path / STDIN_FILE) == MAX_STDIN_BYTES


def test_queue_payload_carries_stdin(monkeypatch: pytest.MonkeyPatch):
    import app.realtime.execution_queue as q

    stored: dict[str, bytes] = {}
    notified: list[bytes] = []

    class FakeRedis:
        def hset(self, _name, field, value):
            stored[field] = value

        def lpush(self, _name, value):
            notified.append(value)

    monkeypatch.setattr(q, "_redis_sync", lambda: FakeRedis())
    q.enqueue_execution_sync(1, 2, 3, "print(input())", stdin="hi\n")
    assert json.loads(stored["1"])["stdin"] == "hi\n"
    assert notified == [b"1"]
