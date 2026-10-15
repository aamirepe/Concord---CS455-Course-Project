"""File CRUD permission-denied paths (create_file's success path is already
exercised indirectly by the `project` fixture reading the bootstrap file)."""
import pytest


@pytest.mark.asyncio
async def test_viewer_cannot_create_file(client, project):
    a, b = project["owner"], project["other"]
    project_id = project["id"]

    r = await client.post(
        f"/projects/{project_id}/members",
        json={"user_id": b["id"], "role": "viewer"},
        headers={"Authorization": f"Bearer {a['token']}"},
    )
    assert r.status_code == 201

    # Viewers can list files...
    r = await client.get(
        f"/projects/{project_id}/files", headers={"Authorization": f"Bearer {b['token']}"}
    )
    assert r.status_code == 200

    # ...but not create new ones.
    r = await client.post(
        f"/projects/{project_id}/files",
        json={"path": "extra.py"},
        headers={"Authorization": f"Bearer {b['token']}"},
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_duplicate_path_conflicts(client, project):
    a = project["owner"]
    project_id = project["id"]
    h = {"Authorization": f"Bearer {a['token']}"}

    # "main.py" already exists (the project's bootstrap file).
    r = await client.post(f"/projects/{project_id}/files", json={"path": "main.py"}, headers=h)
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_non_member_cannot_list_files(client, project):
    b = project["other"]
    r = await client.get(
        f"/projects/{project['id']}/files", headers={"Authorization": f"Bearer {b['token']}"}
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_non_code_files_can_be_created_and_renamed_but_not_run(client, project, no_op_enqueue):
    h = {"Authorization": f"Bearer {project['owner']['token']}"}
    pid = project["id"]
    r = await client.post(f"/projects/{pid}/files", json={"path": "notes.txt"}, headers=h)
    assert r.status_code == 201  # used to be a 500: "Unsupported file extension"
    note = r.json()
    assert note["language"] == "plaintext"

    r = await client.post(f"/projects/{pid}/executions", json={"file_id": note["id"]}, headers=h)
    assert r.status_code == 400
    assert "can't be run" in r.json()["detail"]

    r = await client.patch(f"/projects/{pid}/files/{note['id']}", json={"path": "notes.py"}, headers=h)
    assert r.json()["language"] == "python"
    r = await client.patch(f"/projects/{pid}/files/{note['id']}", json={"path": "README"}, headers=h)
    assert r.json()["language"] == "plaintext"


@pytest.mark.asyncio
async def test_file_move_rejects_folder_from_another_project(client, project):
    owner, other, pid = project["owner"], project["other"], project["id"]
    owner_headers = {"Authorization": f"Bearer {owner['token']}"}
    other_headers = {"Authorization": f"Bearer {other['token']}"}

    file = (await client.get(f"/projects/{pid}/files", headers=owner_headers)).json()[0]
    other_pid = (
        await client.post("/projects", json={"name": "other-project"}, headers=other_headers)
    ).json()["id"]
    other_folder = (
        await client.post(
            f"/projects/{other_pid}/folders", json={"name": "foreign"}, headers=other_headers
        )
    ).json()["id"]

    response = await client.patch(
        f"/projects/{pid}/files/{file['id']}",
        json={"folder_id": other_folder},
        headers=owner_headers,
    )

    assert response.status_code == 404
    files = (await client.get(f"/projects/{pid}/files", headers=owner_headers)).json()
    assert next(item for item in files if item["id"] == file["id"])["folder_id"] == file["folder_id"]


@pytest.mark.asyncio
async def test_file_move_distinguishes_omitted_folder_from_null(client, project):
    owner, pid = project["owner"], project["id"]
    headers = {"Authorization": f"Bearer {owner['token']}"}
    folder = (
        await client.post(f"/projects/{pid}/folders", json={"name": "src"}, headers=headers)
    ).json()["id"]
    file = (
        await client.post(
            f"/projects/{pid}/files",
            json={"path": "move-me.py", "folder_id": folder},
            headers=headers,
        )
    ).json()

    renamed = await client.patch(
        f"/projects/{pid}/files/{file['id']}",
        json={"path": "renamed.py"},
        headers=headers,
    )
    assert renamed.status_code == 200
    assert renamed.json()["folder_id"] == folder

    moved_to_root = await client.patch(
        f"/projects/{pid}/files/{file['id']}",
        json={"folder_id": None},
        headers=headers,
    )
    assert moved_to_root.status_code == 200
    assert moved_to_root.json()["folder_id"] is None
