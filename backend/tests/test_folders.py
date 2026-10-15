"""Folder CRUD, including the permission-denied paths list_folders/create_folder/
update_folder/delete_folder each implement independently (no shared dependency)."""
import pytest


@pytest.mark.asyncio
async def test_folder_crud_round_trip(client, project):
    a = project["owner"]
    project_id = project["id"]
    h = {"Authorization": f"Bearer {a['token']}"}

    r = await client.post(f"/projects/{project_id}/folders", json={"name": "src"}, headers=h)
    assert r.status_code == 201
    folder_id = r.json()["id"]
    assert r.json()["name"] == "src"

    r = await client.get(f"/projects/{project_id}/folders", headers=h)
    assert r.status_code == 200
    assert [f["id"] for f in r.json()] == [folder_id]

    r = await client.patch(
        f"/projects/{project_id}/folders/{folder_id}", json={"name": "lib"}, headers=h
    )
    assert r.status_code == 200
    assert r.json()["name"] == "lib"

    r = await client.delete(f"/projects/{project_id}/folders/{folder_id}", headers=h)
    assert r.status_code == 204

    r = await client.get(f"/projects/{project_id}/folders", headers=h)
    assert r.json() == []


@pytest.mark.asyncio
async def test_viewer_cannot_create_folder(client, project):
    a, b = project["owner"], project["other"]
    project_id = project["id"]

    r = await client.post(
        f"/projects/{project_id}/members",
        json={"user_id": b["id"], "role": "viewer"},
        headers={"Authorization": f"Bearer {a['token']}"},
    )
    assert r.status_code == 201

    # Viewers can list...
    r = await client.get(
        f"/projects/{project_id}/folders", headers={"Authorization": f"Bearer {b['token']}"}
    )
    assert r.status_code == 200

    # ...but not create.
    r = await client.post(
        f"/projects/{project_id}/folders",
        json={"name": "src"},
        headers={"Authorization": f"Bearer {b['token']}"},
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_non_member_cannot_list_folders(client, project):
    b = project["other"]
    r = await client.get(
        f"/projects/{project['id']}/folders", headers={"Authorization": f"Bearer {b['token']}"}
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_debuggers_can_manage_folders_and_files(client, project):
    """Debugger ranks above editor, so it may do whatever an editor may."""
    a, b, pid = project["owner"], project["other"], project["id"]
    h_a = {"Authorization": f"Bearer {a['token']}"}
    h_b = {"Authorization": f"Bearer {b['token']}"}
    r = await client.post(f"/projects/{pid}/members", json={"user_id": b["id"], "role": "debugger"}, headers=h_a)
    assert r.status_code == 201

    r = await client.post(f"/projects/{pid}/folders", json={"name": "lib"}, headers=h_b)
    assert r.status_code == 201
    folder_id = r.json()["id"]
    r = await client.patch(f"/projects/{pid}/folders/{folder_id}", json={"name": "src"}, headers=h_b)
    assert r.status_code == 200
    r = await client.post(f"/projects/{pid}/files", json={"path": "util.py"}, headers=h_b)
    assert r.status_code == 201
    r = await client.patch(f"/projects/{pid}/files/{r.json()['id']}", json={"path": "helpers.py"}, headers=h_b)
    assert r.status_code == 200
    r = await client.delete(f"/projects/{pid}/folders/{folder_id}", headers=h_b)
    assert r.status_code == 204


@pytest.mark.asyncio
async def test_folder_parents_must_be_in_the_project_and_not_loop(client, project):
    a, b, pid = project["owner"], project["other"], project["id"]
    h_a = {"Authorization": f"Bearer {a['token']}"}
    h_b = {"Authorization": f"Bearer {b['token']}"}

    outer = (await client.post(f"/projects/{pid}/folders", json={"name": "outer"}, headers=h_a)).json()["id"]
    inner = (await client.post(f"/projects/{pid}/folders", json={"name": "inner", "parent_id": outer}, headers=h_a)).json()["id"]

    # Into itself, or into its own child.
    r = await client.patch(f"/projects/{pid}/folders/{outer}", json={"parent_id": outer}, headers=h_a)
    assert r.status_code == 400
    r = await client.patch(f"/projects/{pid}/folders/{outer}", json={"parent_id": inner}, headers=h_a)
    assert r.status_code == 400

    # A folder from someone else's project, or one that doesn't exist.
    other_pid = (await client.post("/projects", json={"name": "bobs"}, headers=h_b)).json()["id"]
    theirs = (await client.post(f"/projects/{other_pid}/folders", json={"name": "x"}, headers=h_b)).json()["id"]
    for parent in (theirs, 999_999):
        r = await client.post(f"/projects/{pid}/folders", json={"name": "y", "parent_id": parent}, headers=h_a)
        assert r.status_code == 400
        r = await client.patch(f"/projects/{pid}/folders/{inner}", json={"parent_id": parent}, headers=h_a)
        assert r.status_code == 400

    # A legal move still works.
    third = (await client.post(f"/projects/{pid}/folders", json={"name": "third"}, headers=h_a)).json()["id"]
    r = await client.patch(f"/projects/{pid}/folders/{inner}", json={"parent_id": third}, headers=h_a)
    assert r.status_code == 200
    assert r.json()["parent_folder_id"] == third


@pytest.mark.asyncio
async def test_folder_move_distinguishes_omitted_parent_from_null(client, project):
    owner, pid = project["owner"], project["id"]
    headers = {"Authorization": f"Bearer {owner['token']}"}
    parent = (
        await client.post(f"/projects/{pid}/folders", json={"name": "parent"}, headers=headers)
    ).json()["id"]
    child = (
        await client.post(
            f"/projects/{pid}/folders",
            json={"name": "child", "parent_id": parent},
            headers=headers,
        )
    ).json()

    renamed = await client.patch(
        f"/projects/{pid}/folders/{child['id']}",
        json={"name": "renamed"},
        headers=headers,
    )
    assert renamed.status_code == 200
    assert renamed.json()["parent_folder_id"] == parent

    moved_to_root = await client.patch(
        f"/projects/{pid}/folders/{child['id']}",
        json={"parent_id": None},
        headers=headers,
    )
    assert moved_to_root.status_code == 200
    assert moved_to_root.json()["parent_folder_id"] is None
