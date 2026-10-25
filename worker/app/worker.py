import asyncio
import json
import logging
import os
import socket
import tempfile
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Any

import redis.asyncio as aioredis_async
from sqlalchemy import select, update

from app.core.config import get_settings
from app.db.session import AsyncSessionLocal
from app.debug import control as debug_control
from app.debug.docker_adapter import DockerAdapter
from app.debug.session import DebugOutcome, DebugRun, DebugTarget
from app.models import (
    DebugSession,
    DebugStatus,
    DocumentVersion,
    Execution,
    ExecutionStatus,
    File,
)
from app.realtime.crdt import doc_from_bytes, doc_to_text
from app.realtime.execution_queue import (
    CANCEL_CHANNEL_PREFIX,
    dequeue_execution,
    publish_project_event,
)
from app.services.execution_state import change_status, record_status_event
from app.services.sandbox import prepare_workspace, sandbox

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("worker")

async def _transition(execution_id: int, status: ExecutionStatus, **kwargs) -> bool:
    """Commit only an allowed transition together with its audit event."""
    async with AsyncSessionLocal() as db:
        execution = await db.get(Execution, execution_id)
        if execution and await change_status(db, execution, status, by="worker", **kwargs):
            await db.commit()
            return True
        return False

async def _publish_status(project_id: int, execution_id: int, status: str) -> None:
    """Broadcast status update to the project."""
    await publish_project_event(project_id, {
        "type": "execution_status",
        "execution_id": execution_id,
        "status": status,
    })

def _workspace_path(tmpdir: str, file_path: str) -> Path:
    """Resolve an untrusted project path before any host filesystem write."""
    root = Path(tmpdir).resolve()
    destination = (root / file_path).resolve()
    if Path(file_path).is_absolute() or destination == root or not destination.is_relative_to(root):
        raise ValueError("File path must stay inside the execution workspace")
    return destination

async def _mount_workspace(project_id: int, main_file_id: int, tmpdir: str) -> None:
    """Fetch all files in the project and recreate the folder structure in tmpdir."""
    async with AsyncSessionLocal() as db:
        # 1. Fetch all files for the project
        res = await db.execute(select(File).where(File.project_id == project_id))
        project_files = res.scalars().all()

        # 2. For each file, find the latest version/snapshot
        for file in project_files:
            v_res = await db.execute(
                select(DocumentVersion)
                .where(DocumentVersion.file_id == file.id)
                .order_by(DocumentVersion.version_number.desc())
                .limit(1)
            )
            latest = v_res.scalar_one_or_none()

            content = ""
            if latest:
                content = doc_to_text(doc_from_bytes(latest.snapshot_bytes))

            # 3. Create directory and write file
            full_path = _workspace_path(tmpdir, file.path)
            full_path.parent.mkdir(parents=True, exist_ok=True)
            await asyncio.to_thread(full_path.write_text, content, encoding="utf-8")

class ExecutionCancelled(Exception):
    """The execution was stopped while a sandbox operation was in flight."""


async def _run_cancellable(operation, execution_id: int, cancel_event: asyncio.Event):
    """Join the thread after removing its container, before deleting its workspace.

    Repeating cleanup while Docker creates the container closes the race where
    cancel arrives between scheduling the thread and creating the container.
    """
    if cancel_event.is_set():
        raise ExecutionCancelled
    task = asyncio.create_task(asyncio.to_thread(operation))
    cancel_wait = asyncio.create_task(cancel_event.wait())
    try:
        await asyncio.wait({task, cancel_wait}, return_when=asyncio.FIRST_COMPLETED)
        if cancel_event.is_set():
            while not task.done():
                await asyncio.to_thread(sandbox.kill_execution, execution_id)
                await asyncio.wait({task}, timeout=0.02)
            await asyncio.to_thread(sandbox.kill_execution, execution_id)
            await asyncio.gather(task, return_exceptions=True)
            raise ExecutionCancelled
        return await task
    finally:
        cancel_wait.cancel()
        await asyncio.gather(cancel_wait, return_exceptions=True)


async def _run_one(payload: dict) -> None:
    execution_id = int(payload["execution_id"])
    project_id = int(payload["project_id"])
    file_id = int(payload["file_id"])
    source_text = payload.get("source_text", "")
    debug = payload.get("debug", False)
    language = payload.get("language", "python")
    worker_id = f"{socket.gethostname()}-{os.getpid()}"

    async with AsyncSessionLocal() as db:
        claimed = (await db.execute(
            update(Execution)
            .where(Execution.id == execution_id, Execution.project_id == project_id,
                   Execution.file_id == file_id, Execution.status == ExecutionStatus.QUEUED)
            .values(status=ExecutionStatus.STARTING, worker_id=worker_id,
                    started_at=datetime.now(UTC))
            .returning(Execution.id)
        )).scalar_one_or_none()
        if claimed is not None:
            record_status_event(db, execution_id, ExecutionStatus.QUEUED,
                                ExecutionStatus.STARTING, by="worker", worker_id=worker_id)
        await db.commit()
        if claimed is None:
            log.info("execution=%d is no longer queued; skipping", execution_id)
            return
    await _publish_status(project_id, execution_id, "starting")

    cancel_event = asyncio.Event()
    cancel_sub = None
    pubsub = None
    listener = None
    try:
        try:
            cancel_sub = aioredis_async.from_url(get_settings().redis_url, decode_responses=False)
            pubsub = cancel_sub.pubsub()
            await pubsub.subscribe(CANCEL_CHANNEL_PREFIX + str(execution_id))

            async def listen_cancel() -> None:
                async for msg in pubsub.listen():
                    if msg.get("type") == "message":
                        cancel_event.set()
                        return

            listener = asyncio.create_task(listen_cancel())
        except Exception:
            log.warning("could not subscribe to cancellation", exc_info=True)

        # A cancel may have been persisted before the Redis subscription opened.
        async with AsyncSessionLocal() as db:
            current = await db.get(Execution, execution_id)
            if current is None or current.status != ExecutionStatus.STARTING:
                raise ExecutionCancelled

        from app.runtime.registry import get_runtime_config
        try:
            runtime_config = get_runtime_config(language)
        except ValueError:
            runtime_config = get_runtime_config("python")

        with tempfile.TemporaryDirectory(prefix="concord-exec-") as tmpdir:
            prepare_workspace(tmpdir)
            await _mount_workspace(project_id, file_id, tmpdir)
            async with AsyncSessionLocal() as db:
                file_obj = await db.get(File, file_id)
                if not file_obj or file_obj.project_id != project_id:
                    raise ValueError("Main file not found in DB")
                main_path_rel = file_obj.path
            main_path = _workspace_path(tmpdir, main_path_rel)
            if source_text:
                main_path.write_text(source_text, encoding="utf-8")
                if not debug:
                    _workspace_path(tmpdir, f"main{runtime_config.file_extension}").write_text(source_text, encoding="utf-8")

            if runtime_config.build_command:
                if not await _transition(execution_id, ExecutionStatus.COMPILING):
                    raise ExecutionCancelled
                await _publish_status(project_id, execution_id, "compiling")
                await _run_cancellable(partial(
                    sandbox.build, runtime_config.image, tmpdir, runtime_config.build_command,
                    runtime_config.build_workdir, execution_id=execution_id,
                ), execution_id, cancel_event)

            if not await _transition(execution_id, ExecutionStatus.RUNNING):
                raise ExecutionCancelled
            await _publish_status(project_id, execution_id, "running")
            if debug:
                await _run_debug(payload, project_id, file_id, execution_id, main_path_rel,
                                 tmpdir, runtime_config, cancel_event)
                return
            result = await _run_cancellable(partial(
                sandbox.run, code=source_text, language=language,
                timeout_seconds=get_settings().sandbox_timeout_seconds,
                workspace_dir=tmpdir, stdin=payload.get("stdin"), execution_id=execution_id,
            ), execution_id, cancel_event)
            target = ExecutionStatus.TIMEOUT if result.timed_out else (
                ExecutionStatus.COMPLETED if result.exit_code == 0 else ExecutionStatus.FAILED
            )
            if await _transition(execution_id, target, exit_code=result.exit_code,
                                 stdout=result.stdout, stderr=result.stderr,
                                 finished_at=datetime.now(UTC)):
                await publish_project_event(project_id, {
                    "type": "execution_output", "execution_id": execution_id,
                    "stdout": result.stdout, "stderr": result.stderr,
                    "exit_code": result.exit_code, "duration": result.duration_seconds,
                })
                await _publish_status(project_id, execution_id, target.value)
    except ExecutionCancelled:
        if await _transition(execution_id, ExecutionStatus.CANCELLED,
                             finished_at=datetime.now(UTC)):
            await _publish_status(project_id, execution_id, "cancelled")
    except Exception as exc:
        log.exception("Unexpected error during execution %d", execution_id)
        if await _transition(execution_id, ExecutionStatus.FAILED, stderr=f"Internal worker error: {exc}",
                             finished_at=datetime.now(UTC)):
            await _publish_status(project_id, execution_id, "failed")
    finally:
        if listener is not None:
            listener.cancel()
            await asyncio.gather(listener, return_exceptions=True)
        if pubsub is not None:
            await pubsub.aclose()
        if cancel_sub is not None:
            await cancel_sub.aclose()
        if debug:
            try:
                await debug_control.clear_active(project_id, execution_id)
            except Exception:
                log.warning("could not clear the active debug session", exc_info=True)

async def _run_debug(
    payload: dict, project_id: int, file_id: int, execution_id: int, main_path: str,
    tmpdir: str, runtime_config: Any, cancel_event: asyncio.Event,
) -> None:
    """Run a debug session (Python), relaying commands from Redis, then record the result."""
    settings = get_settings()
    info = payload.get("debug_session_info") or {}
    target = DebugTarget(
        execution_id=execution_id, project_id=project_id, file_id=file_id, path=main_path,
        breakpoints={k: list(v) for k, v in (info.get("breakpoints") or {}).items()},
        file_ids={k: int(v) for k, v in (info.get("file_ids") or {}).items()},
        user_id=info.get("user_id"), username=info.get("username"),
    )
    commands: asyncio.Queue[dict] = asyncio.Queue()
    sub = aioredis_async.from_url(settings.redis_url, decode_responses=True)
    pubsub = sub.pubsub()
    await pubsub.subscribe(debug_control.COMMAND_CHANNEL_PREFIX + str(execution_id))

    async def relay() -> None:
        async for msg in pubsub.listen():
            if msg.get("type") == "message":
                try:
                    commands.put_nowait(json.loads(msg["data"]))
                except ValueError:
                    log.warning("bad debug command: %r", msg.get("data"))

    relay_task = asyncio.create_task(relay())

    async def publish(event: dict) -> None:
        await publish_project_event(project_id, event)

    async def save_state(event: dict | None) -> None:
        await debug_control.set_state(execution_id, event)

    run = DebugRun(
        target, DockerAdapter(sandbox.client, runtime_config.image, tmpdir, execution_id=execution_id), publish, commands,
        total_seconds=settings.sandbox_debug_timeout_seconds,
        idle_seconds=settings.sandbox_debug_idle_seconds,
        save_state=save_state,
    )
    try:
        outcome: DebugOutcome = await run.run(cancel_event)
    finally:
        relay_task.cancel()
        try:
            await pubsub.unsubscribe()
            await sub.close()
        except Exception:
            log.warning("could not close debug command subscription", exc_info=True)
        try:
            await debug_control.clear_active(project_id, execution_id)
        except Exception:
            log.warning("could not clear the active debug session", exc_info=True)

    if outcome.reason in ("stopped", "cancelled"):
        target_status = ExecutionStatus.CANCELLED
    elif outcome.reason in ("timeout", "idle"):
        target_status = ExecutionStatus.TIMEOUT
    elif outcome.reason == "error":
        target_status = ExecutionStatus.FAILED
    else:
        target_status = ExecutionStatus.COMPLETED if outcome.exit_code == 0 else ExecutionStatus.FAILED
    stderr = outcome.stderr
    if outcome.error:
        stderr += f"\n[debugger] {outcome.error}\n"
    elif outcome.reason == "idle":
        stderr += f"\n[debugger] stopped: nobody stepped for {settings.sandbox_debug_idle_seconds} s\n"
    elif outcome.reason == "timeout":
        stderr += f"\n[debugger] stopped after {settings.sandbox_debug_timeout_seconds} s\n"

    now = datetime.now(UTC)
    changed = await _transition(execution_id, target_status, exit_code=outcome.exit_code, stdout=outcome.stdout,
                      stderr=stderr, finished_at=now)
    async with AsyncSessionLocal() as db:
        res = await db.execute(select(DebugSession).where(DebugSession.execution_id == execution_id))
        for ds in res.scalars().all():
            ds.status = DebugStatus.STOPPED
            ds.ended_at = now
        await db.commit()
    if not changed:
        return
    await publish_project_event(project_id, {
        "type": "execution_output", "execution_id": execution_id, "stdout": outcome.stdout,
        "stderr": stderr, "exit_code": outcome.exit_code, "duration": outcome.duration_seconds,
    })
    await _publish_status(project_id, execution_id, target_status.value)


async def main():
    log.info("Starting execution worker...")

    while True:
        try:
            # Recomputes every waiting execution's priority score and takes the
            # highest one (FR-15); a timeout with nothing ready just loops, which
            # also re-checks ageing-driven priority changes even with no new
            # arrivals.
            payload = await dequeue_execution(wait_seconds=1.0)
            if payload:
                await _run_one(payload)
        except Exception as e:
            log.exception("Worker loop error: %s", e)
            await asyncio.sleep(1)

if __name__ == "__main__":
    asyncio.run(main())
