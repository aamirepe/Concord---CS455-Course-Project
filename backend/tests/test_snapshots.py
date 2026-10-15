"""Snapshot create/list, and the editor+ permission-denied path."""
import pytest


@pytest.mark.asyncio
async def test_create_and_list_snapshot(client, project):
    a = project["owner"]
    project_id = project["id"]
    h = {"Authorization": f"Bearer {a['token']}"}

    r = await client.post(f"/projects/{project_id}/snapshots", json={"label": "checkpoint-1"}, headers=h)
    assert r.status_code == 201
    body = r.json()
    assert body["label"] == "checkpoint-1"
    assert body["project_id"] == project_id

    r = await client.get(f"/projects/{project_id}/snapshots", headers=h)
    assert r.status_code == 200
    labels = [s["label"] for s in r.json()]
    assert labels == ["checkpoint-1"]


@pytest.mark.asyncio
async def test_viewer_cannot_create_snapshot(client, project):
    a, b = project["owner"], project["other"]
    project_id = project["id"]

    r = await client.post(
        f"/projects/{project_id}/members",
        json={"user_id": b["id"], "role": "viewer"},
        headers={"Authorization": f"Bearer {a['token']}"},
    )
    assert r.status_code == 201

    r = await client.post(
        f"/projects/{project_id}/snapshots",
        json={"label": "sneaky"},
        headers={"Authorization": f"Bearer {b['token']}"},
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_restore_brings_back_a_file_that_was_empty_and_unopened(client, project, db):
    from app.models import File
    from app.realtime.crdt import doc_to_text
    from app.realtime.rooms import open_room, replace_text

    a = project["owner"]
    project_id = project["id"]
    h = {"Authorization": f"Bearer {a['token']}"}

    # A file nobody has opened yet has no saved version, but it is still part of the project.
    empty = File(project_id=project_id, path="notes.txt", language="python")
    db.add(empty)
    await db.commit()
    await db.refresh(empty)

    r = await client.post(f"/projects/{project_id}/snapshots", json={"label": "before"}, headers=h)
    assert r.status_code == 201
    snapshot_id = r.json()["id"]
    assert r.json()["file_count"] >= 2  # main.py and the empty file

    room = await open_room(project_id, empty.id)
    assert await replace_text(room, "typed later", a["id"])

    r = await client.post(f"/projects/{project_id}/snapshots/{snapshot_id}/restore", headers=h)
    assert r.status_code == 200
    assert r.json()["restored_files"] >= 1
    assert doc_to_text(room.doc) == ""
