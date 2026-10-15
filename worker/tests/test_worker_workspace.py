"""Workspace paths must stay inside the directory mounted into the sandbox."""
from unittest.mock import AsyncMock, Mock

import pytest
from app.core.security import hash_password
from app.models import DocumentVersion, Execution, ExecutionStatus, File, Project, User
from app.realtime.crdt import doc_to_bytes, new_doc
from app.services.sandbox import RunResult

from worker.app import worker


async def _project_file(db, path):
    user = User(username="owner", email="owner@example.com", password_hash=hash_password("pw"))
    db.add(user)
    await db.flush()
    project = Project(owner_id=user.id, name="workspace")
    db.add(project)
    await db.flush()
    file = File(project_id=project.id, path=path, language="python")
    db.add(file)
    await db.flush()
    db.add(DocumentVersion(file_id=file.id, version_number=1,
                           snapshot_bytes=doc_to_bytes(new_doc("print('saved')"))))
    await db.commit()
    return project, file


@pytest.mark.asyncio
@pytest.mark.parametrize("absolute", [False, True])
async def test_mount_rejects_paths_outside_workspace(db, tmp_path, absolute):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    escaped = tmp_path / "escaped.py"
    path = str(escaped) if absolute else "../escaped.py"
    project, file = await _project_file(db, path)

    with pytest.raises(ValueError, match="workspace"):
        await worker._mount_workspace(project.id, file.id, str(workspace))
    assert not escaped.exists()


@pytest.mark.asyncio
async def test_mount_keeps_valid_nested_paths(db, tmp_path):
    project, file = await _project_file(db, "src/main.py")
    await worker._mount_workspace(project.id, file.id, str(tmp_path))
    assert (tmp_path / "src/main.py").read_text() == "print('saved')"


def test_workspace_destination_rejects_symlink_escape_and_root(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "link").symlink_to(tmp_path, target_is_directory=True)
    for path in ("link/escaped.py", "."):
        with pytest.raises(ValueError, match="workspace"):
            worker._workspace_path(str(workspace), path)


@pytest.mark.asyncio
@pytest.mark.parametrize("absolute", [False, True])
async def test_run_validates_main_path_even_when_mount_is_bypassed(
    db, tmp_path, monkeypatch, no_op_publish, absolute,
):
    escaped = tmp_path / "escaped.py"
    path = str(escaped) if absolute else "../escaped.py"
    project, file = await _project_file(db, path)
    execution = Execution(project_id=project.id, file_id=file.id, status=ExecutionStatus.QUEUED)
    db.add(execution)
    await db.commit()
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    class Directory:
        def __enter__(self): return str(workspace)
        def __exit__(self, *args): pass

    monkeypatch.setattr(worker.tempfile, "TemporaryDirectory", lambda **kwargs: Directory())
    monkeypatch.setattr(worker, "_mount_workspace", AsyncMock())
    monkeypatch.setattr(worker.aioredis_async, "from_url", Mock(side_effect=OSError("No Redis")))
    run = Mock(return_value=RunResult(0, "", "", False, 0.01))
    monkeypatch.setattr(worker.sandbox, "run", run)
    await worker._run_one({"execution_id": execution.id, "project_id": project.id,
                           "file_id": file.id, "source_text": "print('queued')"})
    await db.refresh(execution)
    assert execution.status == ExecutionStatus.FAILED
    assert "workspace" in execution.stderr
    assert not escaped.exists()
    run.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [status for status in ExecutionStatus if status != ExecutionStatus.QUEUED])
async def test_worker_does_not_restart_nonqueued_execution(db, monkeypatch, no_op_publish, status):
    project, file = await _project_file(db, "main.py")
    execution = Execution(project_id=project.id, file_id=file.id, status=status)
    db.add(execution)
    await db.commit()
    mount = AsyncMock()
    run = Mock()
    monkeypatch.setattr(worker, "_mount_workspace", mount)
    monkeypatch.setattr(worker.sandbox, "run", run)
    await worker._run_one({"execution_id": execution.id, "project_id": project.id, "file_id": file.id})
    await db.refresh(execution)
    assert execution.status == status
    assert execution.started_at is None
    mount.assert_not_called()
    run.assert_not_called()
    assert no_op_publish == []
