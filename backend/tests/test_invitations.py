"""Invitation create/accept flow, and its permission-denied path."""
import pytest


@pytest.mark.asyncio
async def test_owner_can_invite_and_accept(client, project):
    a, b = project["owner"], project["other"]
    project_id = project["id"]
    h_a = {"Authorization": f"Bearer {a['token']}"}
    h_b = {"Authorization": f"Bearer {b['token']}"}

    r = await client.post(
        "/invitations", params={"project_id": project_id, "role": "editor"}, headers=h_a
    )
    assert r.status_code == 201
    token = r.json()["token"]

    r = await client.post(f"/invitations/accept/{token}", headers=h_b)
    assert r.status_code == 200

    # Bob is now a member with the invited role, and can list project members.
    r = await client.get(f"/projects/{project_id}/members", headers=h_b)
    assert r.status_code == 200
    roles = {m["user"]["id"]: m["role"] for m in r.json()}
    assert roles[b["id"]] == "editor"


@pytest.mark.asyncio
async def test_viewer_cannot_create_invitation(client, project):
    a, b = project["owner"], project["other"]
    project_id = project["id"]

    r = await client.post(
        f"/projects/{project_id}/members",
        json={"user_id": b["id"], "role": "viewer"},
        headers={"Authorization": f"Bearer {a['token']}"},
    )
    assert r.status_code == 201

    r = await client.post(
        "/invitations",
        params={"project_id": project_id, "role": "editor"},
        headers={"Authorization": f"Bearer {b['token']}"},
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_accept_unknown_token_rejected(client, project):
    b = project["other"]
    r = await client.post(
        "/invitations/accept/not-a-real-token",
        headers={"Authorization": f"Bearer {b['token']}"},
    )
    assert r.status_code == 404


async def _add_member(client, project, user, role):
    r = await client.post(
        f"/projects/{project['id']}/members",
        json={"user_id": user["id"], "role": role},
        headers={"Authorization": f"Bearer {project['owner']['token']}"},
    )
    assert r.status_code == 201


@pytest.mark.asyncio
async def test_editor_cannot_invite_above_their_own_role(client, project):
    b = project["other"]
    await _add_member(client, project, b, "editor")
    h_b = {"Authorization": f"Bearer {b['token']}"}
    for role in ("owner", "debugger"):
        r = await client.post("/invitations", params={"project_id": project["id"], "role": role}, headers=h_b)
        assert r.status_code == 403, role
    r = await client.post("/invitations", params={"project_id": project["id"], "role": "editor"}, headers=h_b)
    assert r.status_code == 201


@pytest.mark.asyncio
async def test_unknown_role_is_rejected(client, project):
    h_a = {"Authorization": f"Bearer {project['owner']['token']}"}
    r = await client.post("/invitations", params={"project_id": project["id"], "role": "admin"}, headers=h_a)
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_existing_member_accepting_keeps_one_membership(client, project):
    a, b = project["owner"], project["other"]
    await _add_member(client, project, b, "viewer")
    h_a = {"Authorization": f"Bearer {a['token']}"}
    h_b = {"Authorization": f"Bearer {b['token']}"}
    token = (await client.post("/invitations", params={"project_id": project["id"], "role": "editor"}, headers=h_a)).json()["token"]

    r = await client.post(f"/invitations/accept/{token}", headers=h_b)
    assert r.status_code == 409
    r = await client.post(f"/invitations/accept/{token}", headers=h_a)  # the owner too
    assert r.status_code == 409

    # Bob still has exactly one membership and the project still works for him.
    r = await client.get(f"/projects/{project['id']}/members", headers=h_a)
    assert [m["role"] for m in r.json() if m["user"]["id"] == b["id"]] == ["viewer"]
    assert (await client.post(f"/projects/{project['id']}/join", headers=h_b)).status_code == 200
