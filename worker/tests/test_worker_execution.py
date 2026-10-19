"""Worker execution-loop unit tests, faking the Docker-backed sandbox.

Before this, the worker's BRPOP consumption, workspace mounting, and
status-transition publishing had no unit tests at all — only the resulting
container's security properties were tested (test_sandbox_security.py), and
only under DOCKER_TESTS=1. These tests fake app.services.sandbox.sandbox.run
so the execution loop itself is exercised without a Docker daemon.
"""
from __future__ import annotations

import os
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
    from app.models import ExecutionEvent
    from sqlalchemy import select

    events = (await db.execute(select(ExecutionEvent).where(
        ExecutionEvent.execution_id == execution_id).order_by(ExecutionEvent.id))).scalars().all()
    assert [(event.payload["from"], event.payload["to"]) for event in events] == [
        ("queued", "starting"), ("starting", "running"), ("running", "completed"),
    ]
    assert all(event.payload["by"] == "worker" for event in events)


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


async def _cancel_real_execution(db, no_op_publish, monkeypatch, *, language="python", phase="running"):
    import asyncio
    from dataclasses import replace
    from time import monotonic

    from app.runtime.registry import get_runtime_config
    from app.services.execution_state import change_status
    from app.services.sandbox import sandbox
    from docker.errors import NotFound

    import worker.app.worker as worker

    execution_id, project_id, file_id = await _make_execution(db)
    file = await db.get(File, file_id)
    file.path = "main.cpp" if language == "cpp" else "main.py"
    file.language = language
    await db.commit()

    class CancelBus:
        def __init__(self):
            self.messages = asyncio.Queue()

        def pubsub(self):
            return self

        async def subscribe(self, *_):
            pass

        async def listen(self):
            yield await self.messages.get()

        async def aclose(self):
            pass

    bus = CancelBus()
    monkeypatch.setattr(worker.aioredis_async, "from_url", lambda *a, **kw: bus)
    if phase == "compiling":
        original = get_runtime_config
        config = replace(original("cpp"), build_command=["sh", "-c", "sleep 30; g++ -o /workspace/main.out /workspace/main.cpp"])
        monkeypatch.setattr("app.runtime.registry.get_runtime_config", lambda _: config)
    source = "int main() { while (true) {} }" if language == "cpp" else "while True: pass"
    task = asyncio.create_task(worker._run_one({
        "execution_id": execution_id, "project_id": project_id, "file_id": file_id,
        "source_text": source, "language": language,
    }))
    name = f"concord-{'build' if phase == 'compiling' else 'exec'}-{execution_id}"
    try:
        async def wait_for_container():
            while True:
                if task.done():
                    await task
                    raise AssertionError("Worker ended before the target phase")
                try:
                    container = await asyncio.to_thread(sandbox.client.containers.get, name)
                    if container.status == "running":
                        return
                except NotFound:
                    pass
                await asyncio.sleep(0.02)
        await asyncio.wait_for(wait_for_container(), 20)
        if phase == "running":
            await asyncio.sleep(1.5)
        await db.refresh(await db.get(Execution, execution_id))
        run = await db.get(Execution, execution_id)
        assert run.status == ExecutionStatus(phase)
        assert await change_status(db, run, ExecutionStatus.CANCELLED, by="api")
        await db.commit()
        started = monotonic()
        bus.messages.put_nowait({"type": "message", "data": b"cancel"})
        async def wait_removed():
            while True:
                try:
                    await asyncio.to_thread(sandbox.client.containers.get, name)
                except NotFound:
                    return
                await asyncio.sleep(0.01)
        await asyncio.wait_for(wait_removed(), 1)
        assert monotonic() - started < 1
        await asyncio.wait_for(task, 5)
        await db.refresh(run)
        assert run.status == ExecutionStatus.CANCELLED
        for prefix in ("concord-exec", "concord-build"):
            with pytest.raises(NotFound):
                sandbox.client.containers.get(f"{prefix}-{execution_id}")
    finally:
        bus.messages.put_nowait({"type": "message", "data": b"cancel"})
        await asyncio.to_thread(sandbox.kill_execution, execution_id)
        if not task.done():
            await asyncio.wait_for(task, 5)


@pytest.mark.skipif(os.environ.get("DOCKER_TESTS") != "1", reason="set DOCKER_TESTS=1 to run")
@pytest.mark.asyncio
async def test_cancel_removes_running_container_within_one_second(db, no_op_publish, monkeypatch):
    await _cancel_real_execution(db, no_op_publish, monkeypatch)
