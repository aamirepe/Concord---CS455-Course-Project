"""Starting debug sessions, relaying commands, and what late joiners see."""
from __future__ import annotations

import pytest

from app.debug import control
from app.models import DebugSession


def _h(user: dict) -> dict:
    return {"Authorization": f"Bearer {user['token']}"}


async def _add(client, project: dict, role: str) -> dict:
    other = project["other"]
    r = await client.post(f"/projects/{project['id']}/members", json={"user_id": other["id"], "role": role},
                          headers=_h(project["owner"]))
    assert r.status_code == 201
    return other


@pytest.mark.asyncio
async def test_debug_run_carries_the_projects_breakpoints(client, db, project, no_op_enqueue, fake_redis):
    owner, pid, fid = project["owner"], project["id"], project["file_id"]
    util = (await client.post(f"/projects/{pid}/files", json={"path": "lib/util.py"}, headers=_h(owner))).json()
    await client.put(f"/projects/{pid}/files/{fid}/breakpoints", json={"lines": [5, 2, 5]}, headers=_h(owner))
    await client.put(f"/projects/{pid}/files/{util['id']}/breakpoints", json={"lines": [1]}, headers=_h(owner))

    r = await client.post(f"/projects/{pid}/executions", json={"file_id": fid, "debug": True, "stdin": "x"},
                          headers=_h(owner))
    assert r.status_code == 202
    execution_id = r.json()["id"]

    call = no_op_enqueue[0]
    assert call["debug"] is True and call["stdin"] is None
    info = call["debug_session_info"]
    assert info["breakpoints"] == {"main.py": [2, 5], "lib/util.py": [1]}
    assert info["file_ids"] == {"main.py": fid, "lib/util.py": util["id"]}
    assert info["username"] == owner["username"]

    row = (await db.execute(DebugSession.__table__.select())).mappings().one()
    assert (row["execution_id"], row["project_id"], row["started_by"]) == (execution_id, pid, owner["id"])
    assert (await control.get_active(pid))["execution_id"] == execution_id

    # One session per project at a time.
    r = await client.post(f"/projects/{pid}/debug/start", params={"file_id": fid}, headers=_h(owner))
    assert r.status_code == 409
    assert "already debugging main.py" in r.json()["detail"]


@pytest.mark.asyncio
async def test_a_stale_session_from_a_crashed_worker_is_replaced(client, project, no_op_enqueue, fake_redis):
    pid, fid = project["id"], project["file_id"]
    await control.set_active(pid, {"execution_id": 999, "file_id": fid, "path": "main.py", "username": "ghost"})
    r = await client.post(f"/projects/{pid}/debug/start", params={"file_id": fid}, headers=_h(project["owner"]))
    assert r.status_code == 202
    assert (await control.get_active(pid))["execution_id"] == r.json()["id"]


@pytest.mark.asyncio
async def test_who_may_debug_what(client, project, no_op_enqueue, fake_redis, monkeypatch: pytest.MonkeyPatch):
    pid, fid, owner = project["id"], project["file_id"], project["owner"]
    editor = await _add(client, project, "editor")
    r = await client.post(f"/projects/{pid}/executions", json={"file_id": fid, "debug": True}, headers=_h(editor))
    assert r.status_code == 403
    assert (await client.post(f"/projects/{pid}/executions", json={"file_id": fid}, headers=_h(editor))).status_code == 202

    js = (await client.post(f"/projects/{pid}/files", json={"path": "a.js"}, headers=_h(owner))).json()
    r = await client.post(f"/projects/{pid}/executions", json={"file_id": js["id"], "debug": True}, headers=_h(owner))
    assert r.status_code == 400

    from app.core.config import get_settings
    monkeypatch.setattr(get_settings(), "enable_debugger", False)
    r = await client.post(f"/projects/{pid}/executions", json={"file_id": fid, "debug": True}, headers=_h(owner))
    assert r.status_code == 503


@pytest.mark.asyncio
async def test_commands_reach_the_worker_and_viewers_can_only_look(client, project, no_op_enqueue, fake_redis):
    pid, fid, owner = project["id"], project["file_id"], project["owner"]
    execution_id = (await client.post(f"/projects/{pid}/debug/start", params={"file_id": fid},
                                      headers=_h(owner))).json()["id"]
    url = f"/projects/{pid}/debug/{execution_id}/command"

    r = await client.post(url, json={"command": "next"}, headers=_h(owner))
    assert r.status_code == 202 and r.json() == {"delivered": True}
    channel, cmd = fake_redis.published[-1]
    assert channel == f"concord:debugcmd:{execution_id}"
    assert cmd["command"] == "next" and cmd["username"] == owner["username"]

    viewer = await _add(client, project, "viewer")
    assert (await client.post(url, json={"command": "continue"}, headers=_h(viewer))).status_code == 403
    r = await client.post(url, json={"command": "variables", "ref": 7}, headers=_h(viewer))
    assert r.status_code == 202
    assert fake_redis.published[-1][1]["ref"] == 7

    assert (await client.post(url, json={"command": "reboot"}, headers=_h(owner))).status_code == 422
    r = await client.post(f"/projects/{pid}/debug/{execution_id + 1}/command", json={"command": "next"}, headers=_h(owner))
    assert r.status_code == 409  # not the running session


@pytest.mark.asyncio
async def test_late_joiners_see_where_it_is_paused(client, project, no_op_enqueue, fake_redis):
    pid, owner = project["id"], project["owner"]
    assert (await client.get(f"/projects/{pid}/debug", headers=_h(owner))).json() == {"active": None, "paused": None}
    execution_id = (await client.post(f"/projects/{pid}/debug/start", params={"file_id": project["file_id"]},
                                      headers=_h(owner))).json()["id"]
    await control.set_state(execution_id, {"type": "debug_paused", "line": 4})
    body = (await client.get(f"/projects/{pid}/debug", headers=_h(owner))).json()
    assert body["active"]["execution_id"] == execution_id
    assert body["paused"]["line"] == 4
    assert (await client.get(f"/projects/{pid}/debug", headers=_h(project["other"]))).status_code == 403

    await control.clear_active(pid, execution_id)
    assert (await client.get(f"/projects/{pid}/debug", headers=_h(owner))).json()["active"] is None


@pytest.mark.asyncio
async def test_the_debugger_is_unavailable_without_redis(client, project, no_op_enqueue):
    # conftest points Redis at a closed port
    r = await client.post(f"/projects/{project['id']}/debug/start", params={"file_id": project["file_id"]},
                          headers=_h(project["owner"]))
    assert r.status_code == 503
    r = await client.get(f"/projects/{project['id']}/debug", headers=_h(project["owner"]))
    assert r.json() == {"active": None, "paused": None}
