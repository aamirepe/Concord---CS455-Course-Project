"""Breakpoints: set, clear, share, and reach a running debug session."""
import pytest

from app.debug import control


def _h(user: dict) -> dict:
    return {"Authorization": f"Bearer {user['token']}"}


@pytest.mark.asyncio
async def test_set_clear_and_broadcast(client, project, fake_redis):
    owner, pid, fid = project["owner"], project["id"], project["file_id"]
    url = f"/projects/{pid}/files/{fid}/breakpoints"
    r = await client.put(url, json={"lines": [7, 3, 7, 0]}, headers=_h(owner))
    assert r.status_code == 200
    assert [b["line"] for b in r.json()] == [3, 7]
    assert fake_redis.published[-1][1] == {"type": "breakpoints_updated", "lines_by_file": {fid: [3, 7]}}

    r = await client.put(url, json={"lines": []}, headers=_h(owner))  # the last breakpoint can be removed
    assert r.json() == []
    assert (await client.get(f"/projects/{pid}/breakpoints?file_id={fid}", headers=_h(owner))).json() == []
    assert fake_redis.published[-1][1]["lines_by_file"] == {fid: []}


@pytest.mark.asyncio
async def test_older_bulk_form_replaces_and_can_clear(client, project, fake_redis):
    owner, pid, fid = project["owner"], project["id"], project["file_id"]
    await client.put(f"/projects/{pid}/breakpoints", json=[{"file_id": fid, "line": 1}], headers=_h(owner))
    await client.put(f"/projects/{pid}/breakpoints", json=[{"file_id": fid, "line": 7}], headers=_h(owner))
    lines = [b["line"] for b in (await client.get(f"/projects/{pid}/breakpoints?file_id={fid}", headers=_h(owner))).json()]
    assert lines == [7]
    r = await client.put(f"/projects/{pid}/breakpoints?file_id={fid}", json=[], headers=_h(owner))
    assert r.status_code == 200
    assert (await client.get(f"/projects/{pid}/breakpoints?file_id={fid}", headers=_h(owner))).json() == []


@pytest.mark.asyncio
async def test_a_running_session_gets_new_breakpoints(client, project, fake_redis):
    owner, pid, fid = project["owner"], project["id"], project["file_id"]
    await control.set_active(pid, {"execution_id": 42, "file_id": fid, "path": "main.py", "username": "alice"})
    await client.put(f"/projects/{pid}/files/{fid}/breakpoints", json={"lines": [4]}, headers=_h(owner))
    assert ("concord:debugcmd:42", {"command": "setBreakpoints", "path": "main.py", "lines": [4]}) in fake_redis.published


@pytest.mark.asyncio
async def test_permissions_and_other_projects_files(client, project, fake_redis):
    owner, other, pid, fid = project["owner"], project["other"], project["id"], project["file_id"]
    await client.post(f"/projects/{pid}/members", json={"user_id": other["id"], "role": "editor"}, headers=_h(owner))
    r = await client.put(f"/projects/{pid}/files/{fid}/breakpoints", json={"lines": [3]}, headers=_h(other))
    assert r.status_code == 403  # editors can run, not debug
    assert (await client.get(f"/projects/{pid}/breakpoints?file_id={fid}", headers=_h(other))).status_code == 200

    theirs = (await client.post("/projects", json={"name": "theirs"}, headers=_h(other))).json()
    their_file = (await client.get(f"/projects/{theirs['id']}/files", headers=_h(other))).json()[0]["id"]
    assert (await client.get(f"/projects/{pid}/breakpoints?file_id={their_file}", headers=_h(owner))).status_code == 404
    r = await client.put(f"/projects/{pid}/files/{their_file}/breakpoints", json={"lines": [1]}, headers=_h(owner))
    assert r.status_code == 404
