"""Saving, per-author change attribution, and the recent-changes endpoint."""
from __future__ import annotations

import base64
import json
import time

import pytest
from httpx import AsyncClient
from httpx_ws import aconnect_ws
from httpx_ws.transport import ASGIWebSocketTransport
from pycrdt import Doc, Text
from sqlalchemy import select

import app.realtime.checkpoint as checkpoint_module
import app.realtime.manager as manager_module
from app.models import DocumentVersion, FileChange
from app.realtime.changes import line_stats, split_stats
from app.realtime.checkpoint import checkpoint_once
from app.realtime.crdt import TEXT_TYPE, apply_update_counting, doc_to_text, new_doc
from app.realtime.manager import room_manager


def _auth(user: dict) -> dict:
    return {"Authorization": f"Bearer {user['token']}"}


def _edit(room, user_id: int, change) -> None:
    """Apply an edit to the room's doc the way the gateway does, as ``user_id``."""
    local = Doc()
    local.apply_update(room.doc.get_update())
    before = local.get_state()
    change(local.get(TEXT_TYPE, type=Text))
    room.apply_edit(user_id, bytes(local.get_update(before)))


@pytest.fixture
def broadcasts(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    sent: list[dict] = []
    monkeypatch.setattr(room_manager, "broadcast_to_project", lambda pid, payload, exclude=None: sent.append(json.loads(payload)))
    return sent


async def _live_room(project: dict):
    room = await room_manager.get_or_create_room(project["id"], project["file_id"])
    room.hydrated = True
    return room


# --- line counting -----------------------------------------------------------

def test_line_stats_counts_like_git():
    assert line_stats("a\nb\n", "a\nb\n") == (0, 0)
    assert line_stats("", "a\nb\nc\n") == (3, 0)
    assert line_stats("a\nb\nc\n", "a\n") == (0, 2)
    assert line_stats("a\nb\nc\n", "a\nB\nc\n") == (1, 1)  # edited in place


def test_split_stats_single_author_gets_everything():
    assert split_stats({7: 3}, 10, 4) == {7: (10, 4)}
    assert split_stats({}, 10, 4) == {}


def test_apply_update_counting_counts_inserted_and_deleted_characters():
    doc = new_doc("hello world")
    local = Doc()
    local.apply_update(doc.get_update())
    before = local.get_state()
    text = local.get(TEXT_TYPE, type=Text)
    del text[0:5]
    text.insert(0, "HEY")
    update = bytes(local.get_update(before))
    assert apply_update_counting(doc, update) == 8
    assert apply_update_counting(doc, update) == 0  # already applied


def test_split_stats_shares_by_characters_and_keeps_totals():
    shares = split_stats({1: 300, 2: 100}, 10, 3)
    assert shares[1][0] > shares[2][0]
    assert sum(a for a, _ in shares.values()) == 10
    assert sum(r for _, r in shares.values()) == 3


# --- attribution ---------------------------------------------------------------

@pytest.mark.asyncio
async def test_window_stays_open_while_typing_then_is_recorded(db, project, broadcasts):
    owner = project["owner"]
    room = await _live_room(project)
    _edit(room, owner["id"], lambda t: t.insert(0, "x = 1\ny = 2\n"))

    await checkpoint_once()  # still typing: a version, but no change row yet
    assert (await db.execute(select(FileChange))).first() is None
    assert (await db.execute(select(DocumentVersion))).first() is not None

    room.last_edit = time.time() - checkpoint_module.CHECKPOINT_INTERVAL_SECONDS  # idle now
    await checkpoint_once()
    change = (await db.execute(select(FileChange))).scalar_one()
    assert (change.user_id, change.lines_added, change.lines_removed) == (owner["id"], 2, 0)
    assert broadcasts == [{
        "op": "FILE_CHANGED", "file_id": project["file_id"], "version": 1,  # text unchanged since version 1
        "changes": [{"user_id": owner["id"], "username": owner["username"], "lines_added": 2, "lines_removed": 0}],
    }]


@pytest.mark.asyncio
async def test_a_long_burst_is_closed_after_a_minute(db, project, broadcasts):
    room = await _live_room(project)
    _edit(room, project["owner"]["id"], lambda t: t.insert(0, "a\n"))
    room.window_started = time.time() - checkpoint_module.MAX_WINDOW_SECONDS
    await checkpoint_once()
    assert (await db.execute(select(FileChange))).scalar_one().lines_added == 1


@pytest.mark.asyncio
async def test_two_authors_in_one_window_share_the_lines(db, project, broadcasts):
    owner, other = project["owner"], project["other"]
    room = await _live_room(project)
    _edit(room, owner["id"], lambda t: t.insert(0, "one\ntwo\nthree\n"))
    _edit(room, other["id"], lambda t: t.insert(len(str(t)), "four\n"))

    await checkpoint_once(force=True)
    rows = (await db.execute(select(FileChange))).scalars().all()
    assert {r.user_id for r in rows} == {owner["id"], other["id"]}
    assert sum(r.lines_added for r in rows) == 4


@pytest.mark.asyncio
async def test_people_taking_turns_are_each_credited_exactly(db, project, broadcasts):
    """bob's resynced line, then alice typing a moment later: one line each."""
    owner, other = project["owner"], project["other"]
    room = await _live_room(project)
    _edit(room, other["id"], lambda t: t.insert(0, "a whole line that bob wrote offline\n"))
    room.last_edit -= manager_module.AUTHOR_SWITCH_SECONDS  # alice starts a moment later
    for ch in "x = 1\n":
        _edit(room, owner["id"], lambda t, ch=ch: t.insert(len(str(t)), ch))

    await checkpoint_once(force=True)
    rows = {r.user_id: r.lines_added for r in (await db.execute(select(FileChange))).scalars()}
    assert rows == {other["id"]: 1, owner["id"]: 1}


@pytest.mark.asyncio
async def test_typing_then_undoing_records_nothing(db, project, broadcasts):
    room = await _live_room(project)
    _edit(room, project["owner"]["id"], lambda t: t.insert(0, "oops\n"))
    _edit(room, project["owner"]["id"], lambda t: t.__delitem__(slice(0, 5)))
    await checkpoint_once(force=True)
    assert (await db.execute(select(FileChange))).first() is None
    assert broadcasts == []


@pytest.mark.asyncio
async def test_a_restore_is_credited_to_whoever_restored(db, project, broadcasts):
    from app.realtime.rooms import replace_text

    room = await _live_room(project)
    _edit(room, project["owner"]["id"], lambda t: t.insert(0, "mine\n"))
    await checkpoint_once(force=True)
    await replace_text(room, "restored\nfrom a version\n", project["other"]["id"])
    await checkpoint_once(force=True)
    rows = [(r.user_id, r.lines_added, r.lines_removed) for r in (await db.execute(select(FileChange))).scalars()]
    assert rows == [(project["owner"]["id"], 1, 0), (project["other"]["id"], 2, 1)]
    assert doc_to_text(room.doc) == "restored\nfrom a version\n"


# --- Save ---------------------------------------------------------------------

@pytest.mark.asyncio
async def test_save_writes_a_version_and_records_the_change_now(client: AsyncClient, db, project, broadcasts):
    owner = project["owner"]
    room = await _live_room(project)
    _edit(room, owner["id"], lambda t: t.insert(0, "print('hi')\n"))
    url = f"/projects/{project['id']}/files/{project['file_id']}/save"

    r = await client.post(url, headers=_auth(owner))
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["version"], body["created"]) == (1, True)
    assert body["saved_at"]
    assert (await db.execute(select(FileChange))).scalar_one().lines_added == 1
    assert broadcasts[-1]["op"] == "FILE_CHANGED"

    r = await client.post(url, headers=_auth(owner))  # nothing new
    assert (r.json()["version"], r.json()["created"]) == (1, False)


@pytest.mark.asyncio
async def test_save_of_a_file_nobody_has_open_reports_the_latest_version(client: AsyncClient, project):
    r = await client.post(f"/projects/{project['id']}/files/{project['file_id']}/save", headers=_auth(project["owner"]))
    assert r.status_code == 200
    assert r.json()["created"] is False


@pytest.mark.asyncio
async def test_viewers_and_strangers_cannot_save(client: AsyncClient, project):
    owner, other = project["owner"], project["other"]
    url = f"/projects/{project['id']}/files/{project['file_id']}/save"
    assert (await client.post(url, headers=_auth(other))).status_code == 403  # not a member
    await client.post(f"/projects/{project['id']}/members", json={"user_id": other["id"], "role": "viewer"}, headers=_auth(owner))
    assert (await client.post(url, headers=_auth(other))).status_code == 403
    assert (await client.post(f"/projects/{project['id']}/files/9999/save", headers=_auth(owner))).status_code == 404


# --- recent changes ------------------------------------------------------------

@pytest.mark.asyncio
async def test_activity_lists_recent_files_with_per_author_totals(client: AsyncClient, db, project):
    owner, other = project["owner"], project["other"]
    pid = project["id"]
    await client.post(f"/projects/{pid}/members", json={"user_id": other["id"], "role": "editor"}, headers=_auth(owner))
    ids = [project["file_id"]]
    for name in ("b.py", "c.py"):
        ids.append((await client.post(f"/projects/{pid}/files", json={"path": name}, headers=_auth(owner))).json()["id"])

    from datetime import UTC, datetime, timedelta

    t0 = datetime.now(UTC) - timedelta(hours=1)
    db.add_all([
        FileChange(project_id=pid, file_id=ids[0], user_id=owner["id"], lines_added=5, lines_removed=1, created_at=t0),
        FileChange(project_id=pid, file_id=ids[0], user_id=owner["id"], lines_added=2, lines_removed=0, created_at=t0 + timedelta(minutes=1)),
        FileChange(project_id=pid, file_id=ids[0], user_id=other["id"], lines_added=1, lines_removed=3, created_at=t0 + timedelta(minutes=5)),
        FileChange(project_id=pid, file_id=ids[1], user_id=other["id"], lines_added=9, lines_removed=0, created_at=t0 + timedelta(minutes=2)),
        FileChange(project_id=pid, file_id=ids[2], user_id=owner["id"], lines_added=1, lines_removed=1, created_at=t0 + timedelta(minutes=3)),
    ])
    await db.commit()

    r = await client.get(f"/projects/{pid}/activity", headers=_auth(other))
    assert r.status_code == 200, r.text
    files = r.json()
    assert [f["file_id"] for f in files] == [ids[0], ids[2], ids[1]]  # most recent first
    main = files[0]
    assert (main["path"], main["lines_added"], main["lines_removed"]) == ("main.py", 8, 4)
    assert [(a["username"], a["lines_added"], a["lines_removed"]) for a in main["authors"]] == [("bob", 1, 3), ("alice", 7, 1)]

    r = await client.get(f"/projects/{pid}/activity?limit=1", headers=_auth(owner))
    assert [f["file_id"] for f in r.json()] == [ids[0]]


@pytest.mark.asyncio
async def test_activity_is_empty_for_a_new_project_and_members_only(client: AsyncClient, project):
    pid = project["id"]
    assert (await client.get(f"/projects/{pid}/activity", headers=_auth(project["owner"]))).json() == []
    assert (await client.get(f"/projects/{pid}/activity", headers=_auth(project["other"]))).status_code == 403


# --- gateway -------------------------------------------------------------------

@pytest.mark.asyncio
async def test_gateway_acks_updates_attributes_them_and_ignores_hello(client, project, broadcasts):
    owner = project["owner"]
    from app.main import app

    url = f"/ws/projects/{project['id']}/files/{project['file_id']}?token={owner['token']}"
    async with AsyncClient(transport=ASGIWebSocketTransport(app=app), base_url="http://test") as ws_http:
        async with aconnect_ws(url, ws_http) as ws:
            await ws.receive_bytes()  # initial state
            await ws.send_text(json.dumps({"op": "hello"}))
            await ws.send_text(json.dumps({"op": "project_presence"}))
            local = Doc()
            local.get(TEXT_TYPE, type=Text).insert(0, "a = 1\n")
            await ws.send_text(json.dumps({"op": "update", "data": base64.b64encode(local.get_update()).decode()}))
            assert json.loads(await ws.receive_text()) == {"op": "ack"}  # no "unknown op" errors before it

    assert {"op": "project_presence", "user_id": owner["id"], "file_id": project["file_id"]} in broadcasts
    # Closing the file tells the others they left it.
    assert broadcasts[-1] == {"op": "project_presence", "user_id": owner["id"], "file_id": None,
                              "left_file_id": project["file_id"]}
    room = room_manager.get_room(project["id"], project["file_id"])
    assert room.window_authors.keys() == {owner["id"]}
    assert room.window_text == ""


@pytest.mark.asyncio
async def test_gateway_loads_the_latest_version_once(client, db, project):
    from app.realtime.crdt import doc_to_bytes

    db.add(DocumentVersion(file_id=project["file_id"], version_number=1, snapshot_bytes=doc_to_bytes(new_doc("saved\n"))))
    await db.commit()
    from app.main import app

    url = f"/ws/projects/{project['id']}/files/{project['file_id']}?token={project['owner']['token']}"
    async with AsyncClient(transport=ASGIWebSocketTransport(app=app), base_url="http://test") as ws_http:
        async with aconnect_ws(url, ws_http) as ws:
            first = Doc()
            first.apply_update(await ws.receive_bytes())
            assert str(first.get(TEXT_TYPE, type=Text)) == "saved\n"
    room = room_manager.get_room(project["id"], project["file_id"])
    assert room.hydrated
