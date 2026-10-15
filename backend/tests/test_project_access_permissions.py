"""Role-based permission tests — every mutating endpoint and every WS message
type must enforce ROLE_RANK >= the minimum required.
"""
import pytest


@pytest.mark.asyncio
async def test_non_member_cannot_access_project(client, project):
    a, b = project["owner"], project["other"]
    # The owner can access this same project.
    r = await client.get(
        f"/projects/{project['id']}", headers={"Authorization": f"Bearer {a['token']}"}
    )
    assert r.status_code == 200
    assert r.json()["id"] == project["id"]

    # Bob was never added as a member.
    r = await client.get(
        f"/projects/{project['id']}", headers={"Authorization": f"Bearer {b['token']}"}
    )
    assert r.status_code == 403
