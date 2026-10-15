"""Worker execution-loop unit tests, faking the Docker-backed sandbox.

Before this, the worker's BRPOP consumption, workspace mounting, and
status-transition publishing had no unit tests at all — only the resulting
container's security properties were tested (test_sandbox_security.py), and
only under DOCKER_TESTS=1. These tests fake app.services.sandbox.sandbox.run
so the execution loop itself is exercised without a Docker daemon.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from app.models import Execution, ExecutionStatus, File, Project, User
from app.services.sandbox import RunResult
from sqlalchemy.ext.asyncio import AsyncSession

from worker.app.worker import _run_one


async def _make_execution(db: AsyncSession) -> tuple[int, int, int]:
    from app.core.security import hash_password

    user = User(username="alice", email="alice@example.com", password_hash=hash_password("pw"))
    db.add(user)
    await db.flush()

    project = Project(owner_id=user.id, name="p1")
    db.add(project)
    await db.flush()

    file = File(project_id=project.id, path="main.py", language="python")
    db.add(file)
    await db.flush()

    execution = Execution(project_id=project.id, file_id=file.id, status=ExecutionStatus.QUEUED)
    db.add(execution)
    await db.commit()
    await db.refresh(execution)

    return execution.id, project.id, file.id


@pytest.mark.asyncio
async def test_successful_run_transitions_to_completed(db: AsyncSession, no_op_publish):
    execution_id, project_id, file_id = await _make_execution(db)
    payload = {
        "execution_id": execution_id,
        "project_id": project_id,
        "file_id": file_id,
        "source_text": "print('hi')",
        "language": "python",
        "debug": False,
    }

    fake_result = RunResult(exit_code=0, stdout="hi\n", stderr="", timed_out=False, duration_seconds=0.01)
    with patch("worker.app.worker.sandbox.run", return_value=fake_result) as run_mock:
        await _run_one(payload)

    run_mock.assert_called_once()
    refreshed = await db.get(Execution, execution_id)
    await db.refresh(refreshed)
    assert refreshed.status == ExecutionStatus.COMPLETED
    assert refreshed.exit_code == 0
    assert refreshed.stdout == "hi\n"
    assert refreshed.finished_at is not None

    statuses = [e["status"] for e in no_op_publish if e.get("type") == "execution_status"]
    assert statuses == ["starting", "running", "completed"]


@pytest.mark.asyncio
async def test_timed_out_run_transitions_to_timeout(db: AsyncSession, no_op_publish):
    execution_id, project_id, file_id = await _make_execution(db)
    payload = {
        "execution_id": execution_id,
        "project_id": project_id,
        "file_id": file_id,
        "source_text": "while True: pass",
        "language": "python",
        "debug": False,
    }

    fake_result = RunResult(exit_code=124, stdout="", stderr="", timed_out=True, duration_seconds=10.0)
    with patch("worker.app.worker.sandbox.run", return_value=fake_result):
        await _run_one(payload)

    refreshed = await db.get(Execution, execution_id)
    await db.refresh(refreshed)
    assert refreshed.status == ExecutionStatus.TIMEOUT


@pytest.mark.asyncio
async def test_sandbox_exception_transitions_to_failed(db: AsyncSession, no_op_publish):
    execution_id, project_id, file_id = await _make_execution(db)
    payload = {
        "execution_id": execution_id,
        "project_id": project_id,
        "file_id": file_id,
        "source_text": "print('boom')",
        "language": "python",
        "debug": False,
    }

    with patch(
        "worker.app.worker.sandbox.run", side_effect=RuntimeError("docker daemon unreachable")
    ):
        await _run_one(payload)

    refreshed = await db.get(Execution, execution_id)
    await db.refresh(refreshed)
    assert refreshed.status == ExecutionStatus.FAILED
    assert "docker daemon unreachable" in refreshed.stderr

    statuses = [e["status"] for e in no_op_publish if e.get("type") == "execution_status"]
    assert statuses == ["starting", "running", "failed"]
