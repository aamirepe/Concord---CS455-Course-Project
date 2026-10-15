"""Role-based permission tests — every mutating endpoint and every WS message
type must enforce ROLE_RANK >= the minimum required.
"""
import pytest


@pytest.mark.asyncio
async def test_non_member_cannot_access_project(client, project):
    b = project["other"]
    # Bob was never added as a member.
    r = await client.get(
        f"/projects/{project['id']}", headers={"Authorization": f"Bearer {b['token']}"}
    )
    assert r.status_code == 403
