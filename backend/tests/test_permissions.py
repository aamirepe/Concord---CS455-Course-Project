"""Role-based permission tests — every mutating endpoint and every WS message
type must enforce ROLE_RANK >= the minimum required.
"""
import pytest
from sqlalchemy import select

from app.models import Execution


@pytest.mark.asyncio
async def test_viewer_cannot_run_code(client, project, no_op_enqueue, db):
    a, b = project["owner"], project["other"]
    project_id, file_id = project["id"], project["file_id"]

    # Add Bob to the project as a viewer (the default role).
    r = await client.post(
        f"/projects/{project_id}/members",
        json={"user_id": b["id"], "role": "viewer"},
        headers={"Authorization": f"Bearer {a['token']}"},
    )
    assert r.status_code == 201

    # Bob (viewer) attempts to run code — should be 403.
    r = await client.post(
        f"/projects/{project_id}/executions",
        json={"file_id": file_id},
        headers={"Authorization": f"Bearer {b['token']}"},
    )
    assert r.status_code == 403
    execution_ids = select(Execution.id).where(Execution.project_id == project_id)
    assert (await db.execute(execution_ids)).scalars().all() == []
    assert no_op_enqueue == []

    # Alice (owner) can run.
    r = await client.post(
        f"/projects/{project_id}/executions",
        json={"file_id": file_id},
        headers={"Authorization": f"Bearer {a['token']}"},
    )
    assert r.status_code == 202
    assert r.json()["status"] == "queued"
    assert (await db.execute(execution_ids)).scalars().all() == [r.json()["id"]]
    assert len(no_op_enqueue) == 1
    assert no_op_enqueue[0]["execution_id"] == r.json()["id"]
    assert no_op_enqueue[0]["project_id"] == project_id
    assert no_op_enqueue[0]["file_id"] == file_id
