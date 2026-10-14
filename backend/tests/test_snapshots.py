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
