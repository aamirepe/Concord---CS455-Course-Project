"""Smoke test: run the coding agent against the real Mistral API.

CI never calls Mistral (all agent tests use a scripted provider). Run this by
hand once you have a key, to check that real function calling works end to
end with the current prompts and tools:

    MISTRAL_API_KEY=... PYTHONPATH=backend python scripts/agent_smoke.py

It needs no Docker, Postgres or Redis: it builds a throwaway in-memory SQLite
database with one project, a buggy file and a failed run of it, then asks the
agent to explain the file and to fix the bug. Exits 0 with "skipped" when no
key is set, 1 if a check fails.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from datetime import UTC, datetime

BUGGY = """def average(xs):
    total = 0
    for x in xs:
        total += x
    return total / len(x)


print(average([1, 2, 3]))
"""

STDERR = """Traceback (most recent call last):
  File "/workspace/main.py", line 8, in <module>
    print(average([1, 2, 3]))
  File "/workspace/main.py", line 5, in average
    return total / len(x)
TypeError: object of type 'int' has no len()
"""


async def run(model: str) -> int:
    os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    os.environ.setdefault("SECRET_KEY", "smoke-test-secret-key-32-bytes-min")

    from app.agent.runner import run_agent
    from app.agent.tools import AgentContext
    from app.core.security import hash_password
    from app.llm.adapters import MistralAdapter
    from app.models import DocumentVersion, Execution, ExecutionStatus, File, Project, User
    from app.models.base import Base
    from app.realtime.crdt import doc_to_bytes, new_doc
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    provider = MistralAdapter(api_key=os.environ["MISTRAL_API_KEY"], model=model)
    failures = 0

    async with session_factory() as db:
        user = User(username="smoke", email="smoke@example.com", password_hash=hash_password(os.urandom(8).hex()))
        db.add(user)
        await db.flush()
        project = Project(name="smoke", owner_id=user.id)
        db.add(project)
        await db.flush()
        file = File(project_id=project.id, path="main.py", language="python")
        db.add(file)
        await db.flush()
        db.add(DocumentVersion(file_id=file.id, version_number=1, snapshot_bytes=doc_to_bytes(new_doc(BUGGY))))
        db.add(Execution(
            project_id=project.id, file_id=file.id, status=ExecutionStatus.FAILED,
            queued_at=datetime.now(UTC), exit_code=1, stdout="", stderr=STDERR,
        ))
        await db.commit()

        def ctx() -> AgentContext:
            return AgentContext(db=db, project_id=project.id, active_file_id=file.id, max_file_chars=20000)

        for mode, prompt in [
            ("explain", "Explain this file."),
            ("fix", "My last run failed. Find and fix the bug."),
        ]:
            print(f"\n=== {mode}: {prompt}")
            started = time.monotonic()
            streamed: list[str] = []

            # The same hook the API uses to broadcast AI_STEP over the project socket
            async def on_step(step, started=started, streamed=streamed):
                streamed.append(step.tool)
                print(f"  [{time.monotonic() - started:5.1f}s] step: {step.summary} {'ok' if step.ok else 'FAILED'}")

            agent_ctx = ctx()
            result = await run_agent(agent_ctx, provider, prompt, mode=mode, on_step=on_step)
            if streamed != [s.tool for s in result.steps]:
                print("  CHECK FAILED: streamed steps differ from the result's steps")
                failures += 1
            print(f"  tokens: {result.prompt_tokens}+{result.completion_tokens}  latency: {result.latency_ms} ms")
            print("  answer:\n    " + result.answer.replace("\n", "\n    "))

            if result.truncated:
                print("  CHECK FAILED: ran out of steps")
                failures += 1
            # The active file comes with the first message (or via read_file if it was cut off).
            if file.id not in agent_ctx.files_read:
                print("  CHECK FAILED: never saw the file")
                failures += 1
            if mode == "explain" and result.proposal is not None:
                print("  CHECK FAILED: explain mode proposed an edit")
                failures += 1
            if mode == "fix":
                p = result.proposal
                if p is None:
                    print("  CHECK FAILED: no proposal")
                    failures += 1
                else:
                    print(f"  proposal: {p.original_text!r} -> {p.proposed_text!r} (syntax_ok={p.syntax_ok})")
                    fixed = BUGGY.replace(p.original_text, p.proposed_text, 1)
                    if "len(xs)" not in fixed or not p.syntax_ok:
                        print("  CHECK FAILED: proposal does not fix len(x) -> len(xs)")
                        failures += 1

    await engine.dispose()
    print("\nPASS" if failures == 0 else f"\n{failures} check(s) failed")
    return 0 if failures == 0 else 1


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--model", default=os.environ.get("MISTRAL_MODEL", "codestral-latest"))
    args = parser.parse_args()
    if not os.environ.get("MISTRAL_API_KEY"):
        print("skipped: MISTRAL_API_KEY is not set")
        sys.exit(0)
    import httpx

    try:
        sys.exit(asyncio.run(run(args.model)))
    except httpx.HTTPStatusError as e:
        print(f"Mistral API returned {e.response.status_code}: {e.response.text[:300]}")
        if e.response.status_code == 401:
            print("Check that MISTRAL_API_KEY is correct.")
        sys.exit(1)


if __name__ == "__main__":
    main()
