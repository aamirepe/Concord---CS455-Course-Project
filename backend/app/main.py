"""FastAPI app entrypoint.

Wires:
  - REST routers (auth, projects, files, executions, snapshots, breakpoints, metrics)
  - WebSocket router (collaboration gateway)
  - CORS for the frontend dev server
  - Background task: periodic CRDT checkpointing
  - Startup hooks: ensure DB connectivity, create initial migration if needed
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable
from contextlib import asynccontextmanager
from typing import cast

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.api import (
    activity,
    assistant,
    auth,
    breakpoints,
    executions,
    files,
    folders,
    invitations,
    metrics,
    projects,
    search,
    snapshots,
)
from app.core.config import get_settings
from app.db import session as db_session
from app.realtime.checkpoint import checkpoint_loop, checkpoint_once
from app.realtime.execution_queue import QUEUE_META_HASH, async_redis
from app.realtime.gateway import ws_router
from app.realtime.manager import room_manager
from app.realtime.orphan_sweep import sweep_orphans

log = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s [%(name)s] %(message)s"
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    log.info("Starting %s in env=%s", settings.app_name, settings.app_env)

    checkpoint_task = asyncio.create_task(checkpoint_loop())
    cleanup_task = asyncio.create_task(room_manager.cleanup_loop())
    orphan_sweep_task = asyncio.create_task(sweep_orphans())
    tasks = (checkpoint_task, cleanup_task, orphan_sweep_task)
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        try:
            await asyncio.gather(*tasks, return_exceptions=True)
        except asyncio.CancelledError:
            pass
        # Don't lose the last few seconds of edits (or their attribution) on a restart.
        try:
            await checkpoint_once(force=True)
        except Exception:
            log.exception("final checkpoint failed")


app = FastAPI(
    title="Concord",
    description="Collaborative Code Execution Platform",
    version="0.1.0",
    lifespan=lifespan,
)

settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# REST
app.include_router(auth.router)
app.include_router(projects.router)
app.include_router(folders.router)
app.include_router(invitations.router)
app.include_router(files.router)
app.include_router(snapshots.router)
app.include_router(executions.router)
app.include_router(breakpoints.router)
app.include_router(metrics.router)
app.include_router(assistant.router)
app.include_router(activity.router)
app.include_router(search.router)

# WebSocket
app.include_router(ws_router)


@app.get("/test-me", tags=["meta"])
async def test_me() -> dict:
    return {"status": "ok", "message": "I am alive!"}

@app.get("/healthz", tags=["meta"])
async def healthz() -> JSONResponse:
    database, redis = "down", "down"
    queue_depth = None
    try:
        async with asyncio.timeout(2):
            async with db_session.AsyncSessionLocal() as session:
                await session.execute(text("SELECT 1"))
        database = "ok"
    except Exception:
        log.warning("Health check: database unavailable")
    try:
        async with asyncio.timeout(2):
            client = async_redis()
            await client.ping()
            queue_depth = int(await cast(Awaitable[int], client.hlen(QUEUE_META_HASH)))
        redis = "ok"
    except Exception:
        log.warning("Health check: Redis unavailable")
    return JSONResponse(
        status_code=200 if database == "ok" else 503,
        content={
            "status": "ok" if database == redis == "ok" else "degraded",
            "service": "concord-backend",
            "database": database,
            "redis": redis,
            "queue_depth": queue_depth,
        },
    )
