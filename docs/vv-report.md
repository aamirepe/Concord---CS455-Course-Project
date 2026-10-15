# Concord — Verification & Validation Report

Maps every requirement to the test that proves it, with the actual command
and the expected outcome. Filled in for each phase boundary.

## How to run the full V&V suite

```bash
# Unit + integration tests
docker compose exec backend pytest --cov=app --cov-report=term-missing

# Static analysis
docker compose exec backend ruff check .
docker compose exec backend mypy app
docker compose exec backend bandit -r app -ll

# Sandbox security tests
DOCKER_TESTS=1 docker compose exec backend pytest worker/tests/test_sandbox_security.py

# CRDT convergence (no docker needed)
docker compose exec backend pytest tests/test_concurrent_edits.py

# Full stack: API, WebSockets, sandbox, debugger and edit latency
scripts/pilot.sh up        # later runs: scripts/pilot.sh check
```

### Not covered in this deliverable

Load testing (NFR2) and the worker-kill experiment (FR12) belong to the
Deliverable 3 performance and failure-recovery reports.

## Mapping

| Requirement | Test | Status |
|---|---|---|
| FR1 (auth) | `tests/test_auth.py` | Verified |
| FR2 (project/session CRUD) | `tests/test_auth.py` + manual | Verified |
| FR3 (join by id) | `tests/test_join_requests.py`, `tests/test_project_access_permissions.py::test_non_member_cannot_access_project` | Verified |
| FR4 (latency) | pilot check "Edit latency" (`scripts/latency.py`) | Verified 2026-10-15, fresh clone on a local Docker stack: 3 users, 300/300 edits delivered, p50 1.5 ms, p99 8.4 ms |
| FR5 (convergence) | `tests/test_concurrent_edits.py` | Verified |
| FR6 (snapshot restore) | manual + integration | Verified (manual) |
| FR7 (sandbox) | `worker/tests/test_sandbox_security.py` | Verified |
| FR8 (debugger) | `tests/test_breakpoints.py`, `tests/test_debug.py`, `tests/test_debug_session.py` (scripted adapter, handshake order); `worker/tests/test_debug_session.py` (real sandbox image: breakpoint, locals, frames, expand, step, continue, stop, idle timeout, cancel); `DebugPanel.test.tsx`, `Editor.test.tsx` (margin clicks) | Verified 2026-09-28; also the pilot check "Debug together" and a two-browser run (9/9) |
| FR9 (debug broadcast) | pilot check "Debug together": a second member receives `debug_paused` with locals, sees who stepped, and a late joiner gets the pause from `GET /debug`; `SessionPage.test.tsx` | Verified |
| FR10 (perms) | `tests/test_permissions.py` | Verified |
| FR11 (checkpoint) | `document_versions` rows appear within 5s | Pending (manual) |
| FR12 (chaos) | `tests/test_orphan_sweep.py`; worker-kill experiment (Deliverable 3) | Pending. Blocking bug fixed first: `sweep_orphans()` — the only recovery path for a worker crash — was never started at app startup (`backend/app/main.py`), so FR12 was not actually met. Now wired up + regression-tested (`tests/test_orphan_sweep.py`). Awaiting a real chaos run (see Docker-dependent runs above) |
| FR13 (AI agent) | `tests/test_agent_runner.py`, `tests/test_assistant_api.py`, `tests/test_llm_adapters.py`, `AssistantPanel.test.tsx`, `Editor.test.tsx`; `test_realtime_gateway.py` (AI events over a real project socket); live: `scripts/agent_smoke.py` | Verified with mocks; verified live 2026-09-25 with `codestral-latest`: explain (1 step, ~2.4 s, ~1.1k tokens) and fix (3 steps, ~4.1 s, ~3k tokens, correct `len(x)`→`len(xs)` proposal); full stack on Postgres 16 with a second member receiving `AI_*` events live |
| NFR1 (latency) | pilot check "Edit latency" | Verified with 3 users: p99 8.4 ms (2026-10-15). Larger client counts: Deliverable 3 load test |
| NFR2 (≥10 users) | load test (Deliverable 3) | Pending (Deliverable 3 load test) |
| NFR3 (queue wait) | `/metrics/executions/summary` | Pending. The endpoint previously had no queue-wait field at all (`avg_runtime_seconds` measures execution runtime, not queue wait) and was completely untested — both gaps fixed: added `queue_wait_avg_seconds`/`queue_wait_p95_seconds`, covered by `tests/test_metrics.py`. Awaiting real traffic to read a meaningful p95 from |
| NFR4 (sandbox) | `worker/tests/test_sandbox_security.py` | Verified |
| NFR5 (TLS) | deployment (Deliverable 3) | Pending (deployment) |
| NFR6 (cold restart) | kill backend, restart, verify files load | Pending (manual) |
| NFR7 (CI) | `.github/workflows/ci.yml` | Verified |

## Maintenance / change example — multi-file projects

Original requirement (v1): "a session has a single Python file."

Changed requirement (v1.1): "a session may have multiple files; the editor
opens a file at a time from a tree."

**What changed**
- DB: no schema change — `files.path` already allows multiple files per project
  via `(project_id, path)` uniqueness, and the `file_id` column on `executions`
  was always a foreign key to a specific file, not to a project.
- Backend: added `GET /projects/{id}/files`, `POST /projects/{id}/files`;
  `get_latest_content` now filters by `file_id`.
- Realtime: room key is `(session_id, file_id)` already; no change needed.
- Frontend: added a file tree to the session sidebar; the editor is
  parameterized on the active `file` and reconnects the CRDT WebSocket when
  the active file changes.
- Tests: added `tests/test_breakpoints.py` (file_id is part of the API now).

**Why this proves the maintenance/change requirement:** the change is
deliberately small *because* the original schema and room design were
already file-scoped, not project-scoped. That foresight (one of the
benefits of the v1 design doc) means a 1-day feature addition, not a
re-architecture. The change is documented as a worked example in the
report.

## Failure modes found and fixed

The "bug found → root cause → fix → regression test" template is required
for at least one real issue. Real examples (fill in during the project):

- **Bug:** initial Yjs bindings pushed the entire doc to every new client,
  including a 50KB workspace file, causing 800ms connect latency.
  **Root cause:** we were serializing the whole Y.Doc instead of computing
  a state-vector diff for already-seen clients.
  **Fix:** on connect, client sends its state vector; server responds with
  `get_diff(sv)`, not `get_state()`. Implemented in `gateway.py::collab_socket`.
  **Regression test:** `tests/test_concurrent_edits.py::test_concurrent_edits_converge`
  exercises the diff path; latency now reported via `/metrics/events/recent`.
