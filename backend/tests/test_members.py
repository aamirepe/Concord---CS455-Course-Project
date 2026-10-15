"""Owner-managed membership: POST/PATCH/DELETE /projects/{id}/members."""
from __future__ import annotations

import pytest
from httpx import AsyncClient


def _h(u: dict) -> dict:
    return {"Authorization": f"Bearer {u['token']}"}


async def _role_of(client: AsyncClient, pid: int, owner: dict, user_id: int) -> str | None:
    r = await client.get(f"/projects/{pid}/members", headers=_h(owner))
    assert r.status_code == 200
    return next((m["role"] for m in r.json() if m["user"]["id"] == user_id), None)


@pytest.mark.asyncio
async def test_add_by_username_and_role_is_case_insensitive(client: AsyncClient, project: dict) -> None:
    owner, bob, pid = project["owner"], project["other"], project["id"]
    r = await client.post(f"/projects/{pid}/members", json={"user_id": "bob", "role": "EDITOR"}, headers=_h(owner))
    assert r.status_code == 201
    assert await _role_of(client, pid, owner, bob["id"]) == "editor"

    # Adding again updates the role rather than duplicating the row.
    r = await client.post(f"/projects/{pid}/members", json={"user_id": bob["id"], "role": "viewer"}, headers=_h(owner))
    assert r.status_code == 201
    assert await _role_of(client, pid, owner, bob["id"]) == "viewer"


@pytest.mark.asyncio
async def test_role_defaults_to_viewer(client: AsyncClient, project: dict) -> None:
    owner, bob, pid = project["owner"], project["other"], project["id"]
    r = await client.post(f"/projects/{pid}/members", json={"user_id": bob["id"]}, headers=_h(owner))
    assert r.status_code == 201
    assert await _role_of(client, pid, owner, bob["id"]) == "viewer"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        {"role": "editor"},  # no user
        {"user_id": 2, "role": "admin"},  # not a role
        {"user_id": 2, "role": 5},  # not a string (used to be a 500)
    ],
)
async def test_bad_payloads_are_rejected(client: AsyncClient, project: dict, body: dict) -> None:
    r = await client.post(f"/projects/{project['id']}/members", json=body, headers=_h(project["owner"]))
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_unknown_user_is_404(client: AsyncClient, project: dict) -> None:
    r = await client.post(
        f"/projects/{project['id']}/members", json={"user_id": "nobody"}, headers=_h(project["owner"])
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_owner_cannot_demote_themselves(client: AsyncClient, project: dict, no_op_enqueue: list[dict]) -> None:
    owner, pid = project["owner"], project["id"]
    for user_id in (owner["id"], owner["username"]):
        r = await client.post(f"/projects/{pid}/members", json={"user_id": user_id, "role": "viewer"}, headers=_h(owner))
        assert r.status_code == 400
    r = await client.patch(f"/projects/{pid}/members/{owner['id']}", json={"role": "viewer"}, headers=_h(owner))
    assert r.status_code == 400
    assert await _role_of(client, pid, owner, owner["id"]) == "owner"

    # And the owner can still do owner-level things.
    r = await client.post(f"/projects/{pid}/executions", json={"file_id": project["file_id"]}, headers=_h(owner))
    assert r.status_code == 202


@pytest.mark.asyncio
async def test_only_the_owner_manages_members(client: AsyncClient, project: dict) -> None:
    owner, bob, pid = project["owner"], project["other"], project["id"]
    r = await client.post(f"/projects/{pid}/members", json={"user_id": bob["id"], "role": "editor"}, headers=_h(owner))
    assert r.status_code == 201

    r = await client.post(f"/projects/{pid}/members", json={"user_id": bob["id"], "role": "owner"}, headers=_h(bob))
    assert r.status_code == 403
    r = await client.patch(f"/projects/{pid}/members/{bob['id']}", json={"role": "debugger"}, headers=_h(bob))
    assert r.status_code == 403
    r = await client.delete(f"/projects/{pid}/members/{owner['id']}", headers=_h(bob))
    assert r.status_code == 403

    r = await client.delete(f"/projects/{pid}/members/{owner['id']}", headers=_h(owner))
    assert r.status_code == 400  # the owner can't be removed
    r = await client.delete(f"/projects/{pid}/members/{bob['id']}", headers=_h(owner))
    assert r.status_code == 204
    assert await _role_of(client, pid, owner, bob["id"]) is None
