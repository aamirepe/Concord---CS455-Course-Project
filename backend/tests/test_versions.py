"""Version history (list, read, restore) and snapshots that reach open editors."""
from __future__ import annotations

import pytest
from httpx import AsyncClient
from httpx_ws import aconnect_ws
from httpx_ws.transport import ASGIWebSocketTransport
from pycrdt import Doc, Text
from sqlalchemy import select

from app.models import DocumentVersion, FileChange
from app.realtime.crdt import TEXT_TYPE, doc_to_bytes, doc_to_text, new_doc
from app.realtime.manager import room_manager
from app.realtime.rooms import open_room, replace_text, text_edit_update


def _auth(user: dict) -> dict:
    return {"Authorization": f"Bearer {user['token']}"}


async def _add(client: AsyncClient, project: dict, user: dict, role: str) -> None:
    r = await client.post(f"/projects/{project['id']}/members", json={"user_id": user["id"], "role": role}, headers=_auth(project["owner"]))
    assert r.status_code == 201


async def _versions(db, file_id: int, *texts: str, saved_by: int | None = None) -> None:
    for n, text in enumerate(texts, start=1):
        db.add(DocumentVersion(file_id=file_id, version_number=n, snapshot_bytes=doc_to_bytes(new_doc(text)),
                               saved_by=saved_by if n == len(texts) else None))
    await db.commit()


# --- replace_text --------------------------------------------------------------

@pytest.mark.asyncio
async def test_text_edit_update_is_minimal_and_none_when_equal(project):
    room = await room_manager.get_or_create_room(project["id"], project["file_id"])
    room.doc = new_doc("abc\nKEEP\nxyz\n")
    assert text_edit_update(room, "abc\nKEEP\nxyz\n") is None

    update = text_edit_update(room, "abc\nKEEP!\nxyz\n")
    events = []
    text = room.doc.get(TEXT_TYPE, type=Text)
    sub = text.observe(lambda e: events.append(e.delta))
    room.doc.apply_update(update)
    text.unobserve(sub)
    assert events == [[{"retain": 8}, {"insert": "!"}]]  # only the middle changed


@pytest.mark.asyncio
async def test_open_room_loads_the_latest_version(db, project):
    await _versions(db, project["file_id"], "v1\n", "v2\n")
    room = await open_room(project["id"], project["file_id"])
    assert doc_to_text(room.doc) == "v2\n"
    room.doc = new_doc("live\n")
    assert doc_to_text((await open_room(project["id"], project["file_id"])).doc) == "live\n"  # loaded once


# --- versions API --------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_and_read_versions(client: AsyncClient, db, project):
    owner, fid, pid = project["owner"], project["file_id"], project["id"]
    await _versions(db, fid, "one\n", "two\n", "three\n", saved_by=owner["id"])
    base = f"/projects/{pid}/files/{fid}/versions"

    r = await client.get(base, headers=_auth(owner))
    assert r.status_code == 200
    body = r.json()
    assert [v["version"] for v in body] == [3, 2, 1]
    assert body[0]["saved_by"] == {"id": owner["id"], "username": owner["username"]}
    assert body[1]["saved_by"] is None

    assert [v["version"] for v in (await client.get(f"{base}?saves_only=true", headers=_auth(owner))).json()] == [3]
    assert (await client.get(f"{base}/2", headers=_auth(owner))).json()["text"] == "two\n"
    assert (await client.get(f"{base}/9", headers=_auth(owner))).status_code == 404
    assert (await client.get(base, headers=_auth(project["other"]))).status_code == 403  # not a member
    assert (await client.get(f"/projects/{pid}/files/999/versions", headers=_auth(owner))).status_code == 404


@pytest.mark.asyncio
async def test_restore_reaches_an_open_editor_and_is_recorded(client: AsyncClient, db, project):
    owner, other = project["owner"], project["other"]
    pid, fid = project["id"], project["file_id"]
    await _add(client, project, other, "editor")
    await _versions(db, fid, "good\n", "broken\n")
    from app.main import app

    url = f"/ws/projects/{pid}/files/{fid}?token={owner['token']}"
    async with AsyncClient(transport=ASGIWebSocketTransport(app=app), base_url="http://test") as ws_http:
        async with aconnect_ws(url, ws_http) as ws:
            local = Doc()
            local.apply_update(await ws.receive_bytes())
            assert str(local.get(TEXT_TYPE, type=Text)) == "broken\n"

            r = await client.post(f"/projects/{pid}/files/{fid}/versions/1/restore", headers=_auth(other))
            assert r.status_code == 200, r.text
            assert r.json() == {"restored_from": 1, "version": 3, "changed": True}

            local.apply_update(await ws.receive_bytes())  # arrives like anyone's typing
            assert str(local.get(TEXT_TYPE, type=Text)) == "good\n"

    latest = (await db.execute(select(DocumentVersion).where(DocumentVersion.version_number == 3))).scalar_one()
    assert latest.saved_by == other["id"]
    change = (await db.execute(select(FileChange))).scalar_one()
    assert (change.user_id, change.lines_added, change.lines_removed) == (other["id"], 1, 1)

    r = await client.post(f"/projects/{pid}/files/{fid}/versions/1/restore", headers=_auth(other))
    assert r.json()["changed"] is False


@pytest.mark.asyncio
async def test_viewers_cannot_restore(client: AsyncClient, db, project):
    await _add(client, project, project["other"], "viewer")
    await _versions(db, project["file_id"], "x\n")
    r = await client.post(f"/projects/{project['id']}/files/{project['file_id']}/versions/1/restore", headers=_auth(project["other"]))
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_saving_right_after_an_autosave_marks_it_as_a_save(client: AsyncClient, db, project):
    owner = project["owner"]
    room = await room_manager.get_or_create_room(project["id"], project["file_id"])
    room.hydrated = True
    room.doc = new_doc("x = 1\n")
    from app.realtime.checkpoint import checkpoint_once

    await checkpoint_once()  # autosave v1
    r = await client.post(f"/projects/{project['id']}/files/{project['file_id']}/save", headers=_auth(owner))
    assert (r.json()["version"], r.json()["created"]) == (1, False)
    versions = (await client.get(f"/projects/{project['id']}/files/{project['file_id']}/versions", headers=_auth(owner))).json()
    assert versions[0]["saved_by"]["id"] == owner["id"]


# --- snapshots -------------------------------------------------------------------

@pytest.mark.asyncio
async def test_snapshot_includes_closed_files_and_restore_reaches_open_editors(client: AsyncClient, db, project):
    owner = project["owner"]
    pid, main_id = project["id"], project["file_id"]
    other_id = (await client.post(f"/projects/{pid}/files", json={"path": "util.py"}, headers=_auth(owner))).json()["id"]
    empty_id = (await client.post(f"/projects/{pid}/files", json={"path": "empty.py"}, headers=_auth(owner))).json()["id"]
    await _versions(db, other_id, "closed file v1\n")  # nobody has util.py open
    main_room = await open_room(pid, main_id)
    await replace_text(main_room, "main v1\n", owner["id"])

    r = await client.post(f"/projects/{pid}/snapshots", json={"label": "good state"}, headers=_auth(owner))
    assert r.status_code == 201
    snap = r.json()
    assert (snap["file_count"], snap["created_by_username"]) == (2, owner["username"])  # empty.py skipped
    assert empty_id

    # Things change: main.py live, util.py through a later version.
    await replace_text(main_room, "main v2 broken\n", owner["id"])
    db.add(DocumentVersion(file_id=other_id, version_number=2, snapshot_bytes=doc_to_bytes(new_doc("closed file v2\n"))))
    await db.commit()

    from app.main import app

    url = f"/ws/projects/{pid}/files/{main_id}?token={owner['token']}"
    async with AsyncClient(transport=ASGIWebSocketTransport(app=app), base_url="http://test") as ws_http:
        async with aconnect_ws(url, ws_http) as ws:
            local = Doc()
            local.apply_update(await ws.receive_bytes())
            r = await client.post(f"/projects/{pid}/snapshots/{snap['id']}/restore", headers=_auth(owner))
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["restored_files"] == 2
            local.apply_update(await ws.receive_bytes())
            assert str(local.get(TEXT_TYPE, type=Text)) == "main v1\n"

    r = await client.get(f"/projects/{pid}/files/{other_id}/content", headers=_auth(owner))
    assert r.json()["text"] == "closed file v1\n"

    snaps = (await client.get(f"/projects/{pid}/snapshots", headers=_auth(owner))).json()
    assert [s["label"] for s in snaps] == ["Before restoring 'good state'", "good state"]
    assert snaps[0]["id"] == body["safety_snapshot_id"]


@pytest.mark.asyncio
async def test_restoring_an_unknown_snapshot_is_404(client: AsyncClient, project):
    r = await client.post(f"/projects/{project['id']}/snapshots/999/restore", headers=_auth(project["owner"]))
    assert r.status_code == 404
