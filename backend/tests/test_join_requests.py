"""Request-access → owner approve/reject flow for project membership."""
from __future__ import annotations

import pytest
from httpx import AsyncClient


def _h(u: dict) -> dict:
    return {"Authorization": f"Bearer {u['token']}"}


@pytest.mark.asyncio
async def test_non_member_requests_access_and_owner_approves(client: AsyncClient, project: dict) -> None:
    owner, bob, pid = project["owner"], project["other"], project["id"]

    # Before approval Bob is locked out.
    r = await client.post(f"/projects/{pid}/join", headers=_h(bob))
    assert r.status_code == 403

    r = await client.post(f"/projects/{pid}/join-requests", headers=_h(bob))
    assert r.status_code == 201
    req = r.json()
    assert req["status"] == "pending"
    assert req["user"]["username"] == "bob"

    r = await client.get(f"/projects/{pid}/join-requests", headers=_h(owner))
    assert r.status_code == 200
    assert [x["id"] for x in r.json()] == [req["id"]]

    r = await client.post(
        f"/projects/{pid}/join-requests/{req['id']}/approve", json={"role": "editor"}, headers=_h(owner)
    )
    assert r.status_code == 200
    assert r.json() == {"user": r.json()["user"], "role": "editor"}
    assert r.json()["user"]["id"] == bob["id"]

    r = await client.post(f"/projects/{pid}/join", headers=_h(bob))
    assert r.status_code == 200
    assert r.json()["role"] == "editor"

    # Decided requests drop out of the pending list and can't be decided twice.
    r = await client.get(f"/projects/{pid}/join-requests", headers=_h(owner))
    assert r.json() == []
    r = await client.post(f"/projects/{pid}/join-requests/{req['id']}/reject", headers=_h(owner))
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_approve_defaults_to_viewer(client: AsyncClient, project: dict) -> None:
    owner, bob, pid = project["owner"], project["other"], project["id"]
    req_id = (await client.post(f"/projects/{pid}/join-requests", headers=_h(bob))).json()["id"]

    r = await client.post(f"/projects/{pid}/join-requests/{req_id}/approve", json={}, headers=_h(owner))
    assert r.status_code == 200
    assert r.json()["role"] == "viewer"


@pytest.mark.asyncio
async def test_approve_cannot_grant_owner_role(client: AsyncClient, project: dict) -> None:
    owner, bob, pid = project["owner"], project["other"], project["id"]
    req_id = (await client.post(f"/projects/{pid}/join-requests", headers=_h(bob))).json()["id"]

    r = await client.post(
        f"/projects/{pid}/join-requests/{req_id}/approve", json={"role": "owner"}, headers=_h(owner)
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_rejected_user_stays_out_but_can_ask_again(client: AsyncClient, project: dict) -> None:
    owner, bob, pid = project["owner"], project["other"], project["id"]
    req_id = (await client.post(f"/projects/{pid}/join-requests", headers=_h(bob))).json()["id"]

    r = await client.post(f"/projects/{pid}/join-requests/{req_id}/reject", headers=_h(owner))
    assert r.status_code == 200
    assert r.json()["status"] == "rejected"
    assert r.json()["decided_at"] is not None

    r = await client.post(f"/projects/{pid}/join", headers=_h(bob))
    assert r.status_code == 403

    r = await client.post(f"/projects/{pid}/join-requests", headers=_h(bob))
    assert r.status_code == 201


@pytest.mark.asyncio
async def test_duplicate_pending_request_conflicts(client: AsyncClient, project: dict) -> None:
    bob, pid = project["other"], project["id"]
    assert (await client.post(f"/projects/{pid}/join-requests", headers=_h(bob))).status_code == 201

    r = await client.post(f"/projects/{pid}/join-requests", headers=_h(bob))
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_existing_member_cannot_request(client: AsyncClient, project: dict) -> None:
    r = await client.post(f"/projects/{project['id']}/join-requests", headers=_h(project["owner"]))
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_request_for_missing_project_is_404(client: AsyncClient, project: dict) -> None:
    r = await client.post("/projects/999999/join-requests", headers=_h(project["other"]))
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_only_owner_can_list_or_decide(client: AsyncClient, project: dict) -> None:
    owner, bob, pid = project["owner"], project["other"], project["id"]
    req_id = (await client.post(f"/projects/{pid}/join-requests", headers=_h(bob))).json()["id"]

    # Even an approved non-owner member can't review requests.
    r = await client.get(f"/projects/{pid}/join-requests", headers=_h(bob))
    assert r.status_code == 403
    r = await client.post(f"/projects/{pid}/join-requests/{req_id}/approve", json={}, headers=_h(bob))
    assert r.status_code == 403
    r = await client.post(f"/projects/{pid}/join-requests/{req_id}/reject", headers=_h(bob))
    assert r.status_code == 403

    # The request is untouched.
    r = await client.get(f"/projects/{pid}/join-requests", headers=_h(owner))
    assert [x["id"] for x in r.json()] == [req_id]


@pytest.mark.asyncio
async def test_request_id_from_another_project_is_404(client: AsyncClient, project: dict) -> None:
    owner, bob, pid = project["owner"], project["other"], project["id"]
    req_id = (await client.post(f"/projects/{pid}/join-requests", headers=_h(bob))).json()["id"]
    other_pid = (await client.post("/projects", json={"name": "p2"}, headers=_h(owner))).json()["id"]

    r = await client.post(f"/projects/{other_pid}/join-requests/{req_id}/approve", json={}, headers=_h(owner))
    assert r.status_code == 404
