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

# CRDT convergence (no docker needed — pure in-process check)
python scripts/load_test.py --clients 1 5 10 25
```

### Docker-dependent runs (Phase 6 — fills FR4, NFR1, NFR2, FR12 below)

`scripts/load_test.py --wire` (via `scripts/latency.py`) speaks the browser's
protocol (base64 JSON updates up, binary CRDT updates down). Each client logs
in as a user and types unique tokens into one file; every *other* client
records when each token reaches its copy, so the numbers are "A types, B sees
it" on one clock. It fails when any edit is lost or p99 ≥ 300 ms. (Before
2026-09-28 it sent raw binary frames, which the gateway ignores, measured
nothing and still exited 0.) The pilot's "Edit latency" check runs the same
code with 3 users.

```bash
# 1. Bring up the full stack with demo accounts
scripts/pilot.sh up

# 2. Wire-protocol latency test (fills FR4 / NFR1 / NFR2); any file the
#    accounts can edit, e.g. check_latency.txt in the "Pilot check" project
docker compose exec -e PYTHONPATH=/app backend python /scripts/load_test.py \
  --wire --clients 2 10 25 --edits 40 --project <id> --file <id>

# 3. Chaos test: needs a token, project id and file id
export CONCORD_TOKEN=... CONCORD_PROJECT_ID=... CONCORD_FILE_ID=...

# 4. Chaos test (fills FR12) — requires Part A's orphan-sweep fix to be
#    deployed; takes ~5-6 minutes since the sweep only acts on executions
#    stuck for 5+ minutes
docker compose exec backend python scripts/chaos.py --api http://backend:8000 \
  --token $CONCORD_TOKEN --project-id $CONCORD_PROJECT_ID --file-id $CONCORD_FILE_ID
# when it prompts, run in another terminal:
docker compose kill -s SIGKILL worker
docker compose up -d worker   # bring replicas back afterwards
```

## Mapping

| Requirement | Test | Status |
|---|---|---|
| FR1 (auth) | `tests/test_auth.py` | ✅ |
| FR2 (project/session CRUD) | `tests/test_auth.py` + manual | ✅ |
| FR3 (join by id) | `tests/test_permissions.py::test_non_member_cannot_access_session` | ✅ |
| FR4 (latency) | `scripts/load_test.py --wire`; pilot check "Edit latency" | ✅ 2026-09-28, local Docker stack: 3 users × 50 edits, 300/300 delivered, p50 1.9 ms, p99 3.6 ms |
| FR5 (convergence) | `tests/test_concurrent_edits.py` + `scripts/load_test.py` | ✅ — re-confirmed with real in-process numbers at N=1,5,10,25 (all converged; see below) |
| FR6 (snapshot restore) | manual + integration | ✅ (manual) |
| FR7 (sandbox) | `worker/tests/test_sandbox_security.py` | ✅ |
| FR8 (debugger) | `tests/test_breakpoints.py`, `tests/test_debug.py`, `tests/test_debug_session.py` (scripted adapter, handshake order); `worker/tests/test_debug_session.py` (real sandbox image: breakpoint, locals, frames, expand, step, continue, stop, idle timeout, cancel); `DebugPanel.test.tsx`, `Editor.test.tsx` (margin clicks) | ✅ 2026-09-28; also the pilot check "Debug together" and a two-browser run (9/9) |
| FR9 (debug broadcast) | pilot check "Debug together": a second member receives `debug_paused` with locals, sees who stepped, and a late joiner gets the pause from `GET /debug`; `SessionPage.test.tsx` | ✅ |
| FR10 (perms) | `tests/test_permissions.py` | ✅ |
| FR11 (checkpoint) | `document_versions` rows appear within 5s | ⏳ manual |
| FR12 (chaos) | `scripts/chaos.py` | ⏳ blocking bug fixed first: `sweep_orphans()` — the only recovery path for a worker crash — was never started at app startup (`backend/app/main.py`), so FR12 was not actually met. Now wired up + regression-tested (`tests/test_orphan_sweep.py`). Awaiting a real chaos run (see Docker-dependent runs above) |
| FR13 (AI agent) | `tests/test_agent_runner.py`, `tests/test_assistant_api.py`, `tests/test_llm_adapters.py`, `AssistantPanel.test.tsx`, `Editor.test.tsx`; `test_realtime_gateway.py` (AI events over a real project socket); live: `scripts/agent_smoke.py` | ✅ mocked; ✅ live 2026-09-25 with `codestral-latest`: explain (1 step, ~2.4 s, ~1.1k tokens) and fix (3 steps, ~4.1 s, ~3k tokens, correct `len(x)`→`len(xs)` proposal); full stack on Postgres 16 with a second member receiving `AI_*` events live |
| NFR1 (latency) | `load_test.py --wire` | ✅ p99 3.0 ms (2 clients), 16 ms (10), 74 ms (25): all under 300 ms, no lost edits |
| NFR2 (≥10 users) | `load_test.py --wire` | ✅ 10 clients: 3600/3600 deliveries, p99 16 ms; 25 clients: 24000/24000, p99 74 ms (the 25 clients share one Python process, so part of that is client-side) |
| NFR3 (queue wait) | `/metrics/executions/summary` | ⏳ endpoint previously had no queue-wait field at all (`avg_runtime_seconds` measures execution runtime, not queue wait) and was completely untested — both gaps fixed: added `queue_wait_avg_seconds`/`queue_wait_p95_seconds`, covered by `tests/test_metrics.py`. Awaiting real traffic to read a meaningful p95 from |
| NFR4 (sandbox) | `test_sandbox_security.py` | ✅ |
| NFR5 (TLS) | Caddy auto-cert | ⏳ deploy |
| NFR6 (cold restart) | kill backend, restart, verify files load | ⏳ manual |
| NFR7 (CI) | `.github/workflows/ci.yml` | ✅ |

### FR5 real numbers (in-process convergence, no Docker needed)

```
{"clients": 1,  "total_edits": 50,   "duration_seconds": 0.001, "converged": true}
{"clients": 5,  "total_edits": 250,  "duration_seconds": 0.011, "converged": true}
{"clients": 10, "total_edits": 500,  "duration_seconds": 0.046, "converged": true}
{"clients": 25, "total_edits": 1250, "duration_seconds": 0.638, "converged": true}
```

All four runs converged to byte-identical state. This confirms CRDT
correctness at higher client counts than the existing 10x100
`test_concurrent_edits.py` test uses, but — per the script's own docstring —
does **not** measure real network propagation latency (no WebSocket, no
docker), so it doesn't fill FR4/NFR1/NFR2; those need the `--wire` run above.

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
