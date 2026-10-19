# Concord - Verification and Validation Report

This report uses the same 26 functional and 38 non-functional identifiers as
[the requirements specification](requirements.md). Each row identifies the
test or pilot check, its Jira work and the limits of the available evidence.

## Evidence baseline

This is an evidence snapshot as of 2026-10-20. Main remains at `2abc43f`.
[PR #24](https://github.com/aamirepe/Concord---CS455-Course-Project/pull/24)
adds path validation, login throttling and `backend/tests/test_adversarial_api.py`.
Its backend, worker, frontend and e2e GitHub checks passed. Review and merge are
pending. References to other pending extensions are planned checks, not claims
that their code or results are already on main.

Functional tests establish the scenarios they exercise. Performance, availability,
browser compatibility and operator targets need their stated measurements.
A passing unit suite or a Jira Done status does not supply those measurements.

## Commands

Run backend checks from `backend/` in the Python 3.12 environment:

```bash
pytest -q --cov=app --cov-report=term-missing
ruff check .
mypy app
bandit -q -r app -ll
```

Run real sandbox tests from the repository root after building the three images:

```bash
docker build -t concord-sandbox:latest ./sandbox
docker build -t concord-sandbox-cpp:latest -f sandbox/Dockerfile.cpp ./sandbox
docker build -t concord-sandbox-nodejs:latest -f sandbox/Dockerfile.nodejs ./sandbox
DOCKER_TESTS=1 PYTHONPATH=backend pytest worker/tests/ --import-mode=importlib -q
```

Provide the normal application configuration and a test secret through the
environment. Do not copy a production API key into tests. The worker command is
for a POSIX shell or the Linux CI environment.

From `frontend/`, run `npm ci`, `npm run lint`, `npm run typecheck`,
`npm run coverage` and `npm run build`. For the Docker stack and seeded
multi-user checks, run `scripts/pilot.sh up`, then `scripts/pilot.sh check`.
The e2e job in `.github/workflows/ci.yml` documents the browser-test container.

PR #24 was also checked locally on Windows: 263 backend tests passed and the
same two baseline sandbox-workspace tests failed because Windows lacks
`os.chown`. Full mypy reports that same platform attribute; changed source
modules pass mypy. Linux GitHub CI passed the full configured checks. These
results describe PR #24 and are not a fresh run of a later merged repository.

## Functional requirement mapping

| Requirement | Jira tasks | Verification test or pilot check | Evidence status |
|---|---|---|---|
| FR-01 | CONCORD-23, CONCORD-24, CONCORD-66, CONCORD-73 | `backend/tests/test_auth.py`, `backend/tests/test_gateway_authorization.py`; token attacks and throttling in PR #24. | Existing auth checks passed; PR #24 adds hostile-token and throttling evidence, pending merge. |
| FR-02 | CONCORD-27 | `frontend/src/pages/DashboardPage.test.tsx`; pilot project creation check. | Functional evidence exists; creation/ownership checked by pilot and client tests. |
| FR-03 | CONCORD-29 | `backend/tests/test_invitations.py`. | Invitation role/expiry checks covered by CI. |
| FR-04 | CONCORD-29 | `backend/tests/test_join_requests.py`. | Approve/reject/duplicate-request checks covered by CI. |
| FR-05 | CONCORD-26 | `backend/tests/test_members.py`. | Member-management and protected-owner checks covered by CI. |
| FR-06 | CONCORD-25, CONCORD-73, CONCORD-76 | `backend/tests/test_permissions.py`, `backend/tests/test_project_access_permissions.py`, `backend/tests/test_gateway_authorization.py`, `backend/tests/test_realtime_gateway.py`; route attacks in PR #24; browser extension pending. | Existing role checks passed; full route inventory attacks in PR #24 await merge. |
| FR-07 | CONCORD-28, CONCORD-65 | `backend/tests/test_files.py`, `backend/tests/test_folders.py`, `frontend/src/components/FileTree.test.tsx`; path validation in PR #24. Individual file deletion remains a gap: no DELETE file route exists. | Partial: tree/create/rename/move/folder-delete covered; individual file deletion absent; path fix awaits merge. |
| FR-08 | CONCORD-30, CONCORD-31, CONCORD-69 | `backend/tests/test_concurrent_edits.py`, `backend/tests/test_realtime_gateway.py`; larger gateway concurrency tests pending. | Existing CRDT/gateway checks passed; larger gateway concurrency extension pending. |
| FR-09 | CONCORD-33 | `backend/tests/test_awareness.py`, `frontend/src/components/Editor.test.tsx`; two-browser pilot check. | Awareness behaviour covered; visual multi-client pilot evidence recorded. |
| FR-10 | CONCORD-38, CONCORD-61 | `backend/tests/test_execution_state_machine.py`, `worker/tests/test_worker_execution.py`; transition-enforcement fix pending. | Partial: state model tested; checked persistence of every transition pending CONCORD-61. |
| FR-11 | CONCORD-35, CONCORD-38, CONCORD-73 | `worker/tests/test_worker_execution.py`, `backend/tests/test_run_input.py`; input attacks in PR #24. | Language execution covered by worker CI; stdin boundary attack in PR #24 awaits merge. |
| FR-12 | CONCORD-39, CONCORD-41, CONCORD-67, CONCORD-76 | `frontend/src/pages/SessionPage.test.tsx`, `frontend/src/components/ConsolePanel.test.tsx`; pilot shared-run check; lifecycle/event-log extensions pending. | Shared results covered by client/pilot checks; full lifecycle timing/event-log extension pending. |
| FR-13 | CONCORD-40, CONCORD-60, CONCORD-75 | `backend/tests/test_permissions.py`, `worker/tests/test_worker_execution.py`; running-container removal and phase-specific cancellation tests pending. | Partial: cancellation API covered; proof of prompt running-container removal pending. |
| FR-14 | CONCORD-37, CONCORD-69 | `backend/tests/test_priority_queue.py`, `worker/tests/test_worker_execution.py`; concurrent claim verification pending. | Queue/worker mechanisms tested; high-contention claim verification pending. |
| FR-15 | CONCORD-42, CONCORD-43, CONCORD-69 | `backend/tests/test_priority_queue.py`; concurrent claim tests pending. | Priority calculation/order tested; concurrent-dequeue extension pending. |
| FR-16 | CONCORD-40, CONCORD-75 | `backend/tests/test_orphan_sweep.py`; real worker-kill timing pending. Active debug sessions use their separate timeout/recovery grace. | Sweep unit checks passed; wall-clock recovery after real worker failure pending. |
| FR-17 | CONCORD-44, CONCORD-45 | `backend/tests/test_dap_client.py`, `backend/tests/test_breakpoints.py`, `backend/tests/test_debug.py`, `worker/tests/test_debug_session.py`; pilot Debug together check. | DAP and real sandbox worker checks passed; pilot Debug together evidence recorded. |
| FR-18 | CONCORD-46, CONCORD-47, CONCORD-69, CONCORD-71 | `backend/tests/test_debug.py`, `backend/tests/test_debug_session.py`, `frontend/src/components/DebugPanel.test.tsx`; concurrent-start and latency extensions pending. | Functional slot/role/state checks exist; concurrent-start and latency extension pending. |
| FR-19 | CONCORD-52 | `backend/tests/test_snapshots.py`, `frontend/src/components/SnapshotsPanel.test.tsx`. | Snapshot safety/restore behaviour covered by backend/client tests. |
| FR-20 | CONCORD-48 | `backend/tests/test_llm_adapters.py`, `backend/tests/test_assistant_api.py`. | Provider adapters, encryption/configuration and owner restrictions covered. |
| FR-21 | CONCORD-49, CONCORD-50, CONCORD-74 | `backend/tests/test_agent_runner.py`, `backend/tests/test_assistant_api.py`, `frontend/src/components/AssistantPanel.test.tsx`; adversarial extension pending. | Bounded tool contracts/human acceptance covered; additional adversarial audit checks pending. |
| FR-22 | CONCORD-51, CONCORD-64 | `backend/tests/test_assistant_api.py`; tool-step audit extension pending. | Request audit covered; persisted per-tool steps pending CONCORD-64. |
| FR-23 | CONCORD-55, CONCORD-63 | `backend/tests/test_metrics.py`; authenticated project scoping pending. | Summary/recent metrics covered; access-control fix pending CONCORD-63. |
| FR-24 | CONCORD-51 | `backend/tests/test_llm_manager.py`, `backend/tests/test_assistant_api.py`. | Redis limit/reset behaviour covered with controlled dependencies. |
| FR-25 | CONCORD-53 | `backend/tests/test_versions.py`, `backend/tests/test_file_changes.py`, `frontend/src/components/HistoryModal.test.tsx`. | Version/restore and history UI covered. |
| FR-26 | CONCORD-53 | `backend/tests/test_search.py`, `frontend/src/components/SearchPanel.test.tsx`. | Cross-file text search and UI covered. |

## Non-functional requirement mapping

| Requirement | Jira tasks | Verification test or pilot check | Evidence status |
|---|---|---|---|
| NFR-U1 | CONCORD-34, CONCORD-56, CONCORD-76 | `e2e/test_navigation.py`, `e2e/test_collaboration.py`; four-browser matrix pending. | Partial: CI browser tests do not prove all four target browsers. |
| NFR-U2 | CONCORD-33 | `backend/tests/test_awareness.py`, `frontend/src/components/Editor.test.tsx`; visual pilot check. | Behaviour covered; confirm colours/names/selections visually in pilot. |
| NFR-U3 | CONCORD-38, CONCORD-34 | `worker/tests/test_worker_execution.py`, `frontend/src/components/ConsolePanel.test.tsx`; error-display pilot check. | Error results/rendering covered; inspect representative line references in pilot. |
| NFR-U4 | CONCORD-34, CONCORD-56 | `frontend/src/pages/SessionPage.test.tsx`, `e2e/test_navigation.py`; timed new-user study pending. | Layout covered; three-minute novice usability target unmeasured. |
| NFR-P1 | CONCORD-31, CONCORD-57, CONCORD-69 | `backend/tests/test_realtime_gateway.py`; `scripts/latency.py` has recorded three-client evidence only; ten-client measurement pending. | Partial: recorded three-client p99 8.4 ms; ten-client target unverified. |
| NFR-P2 | CONCORD-55, CONCORD-57, CONCORD-67 | `backend/tests/test_metrics.py`, `backend/tests/test_priority_queue.py`; five-request burst measurement pending. | Metric calculation covered; five-request p95 target unmeasured. |
| NFR-P3 | CONCORD-46, CONCORD-67, CONCORD-71 | `backend/tests/test_debug_session.py` verifies functionality; latency target unmet pending optimisation/measurement. | Target not met; optimisation and repeated multi-client measurement pending. |
| NFR-P4 | CONCORD-34, CONCORD-68 | `frontend/src/components/Editor.test.tsx` verifies behaviour only; browser frame-time profile at or below 16.6 ms pending. | Unverified: no typing FPS/frame-time capture; unit tests do not measure rendering speed. |
| NFR-S1 | CONCORD-36 | `worker/tests/test_sandbox_security.py`; real-container resource checks. | Container security suite passed in PR #24 CI; memory-limit mechanism covered. |
| NFR-S2 | CONCORD-36 | `worker/tests/test_sandbox_security.py`; quota configuration and real-container inspection. | Configured quota supported by container checks; CPU scheduling performance is not inferred. |
| NFR-S3 | CONCORD-34, CONCORD-72 | Frontend build in `.github/workflows/ci.yml`; compressed-size measurement pending. | Build passed; compressed bundle threshold unmeasured. |
| NFR-S4 | CONCORD-53 | `backend/tests/test_version_pruning.py`. | Retention/pruning rules covered by unit/integration checks. |
| NFR-D1 | CONCORD-54, CONCORD-70, CONCORD-68 | Health/deployment checks support monitoring; laboratory uptime observation window and record pending; unverified. | Unverified: no scheduled-laboratory uptime sample or availability calculation. |
| NFR-D2 | CONCORD-32, CONCORD-75 | `backend/tests/test_checkpoint.py`; abrupt-restart timing and recovery measurement pending. | Checkpoint mechanism covered; measured crash-loss/restart bound pending. |
| NFR-D3 | CONCORD-30, CONCORD-31, CONCORD-69 | `backend/tests/test_concurrent_edits.py`, `backend/tests/test_realtime_gateway.py`; larger gateway tests pending. | Covered for existing simulated/gateway cases; larger concurrent gateway extension pending. |
| NFR-D4 | CONCORD-40, CONCORD-75 | `backend/tests/test_orphan_sweep.py`; worker-kill timing pending; active debugging uses its documented longer timeout. | Stale sweep logic covered; real six-minute recovery measurement pending. |
| NFR-D5 | CONCORD-42, CONCORD-43, CONCORD-69 | `backend/tests/test_priority_queue.py`; concurrent claims/races pending. Running work is not preempted by later arrivals. | Priority logic covered; contention/double-allocation experiment pending. |
| NFR-SEC1 | CONCORD-36, CONCORD-62 | `worker/tests/test_sandbox_security.py`, `worker/tests/test_worker_workspace.py`; capability/no-new-privileges extension pending. | Partial: existing isolation checks passed; capability drop/no-new-privileges proof pending. |
| NFR-SEC2 | CONCORD-23, CONCORD-66 | `backend/tests/test_auth.py`; auth/logging source inspection; throttling in PR #24. | Hashing/auth behaviour covered; source/log review required to confirm secrets are not logged. |
| NFR-SEC3 | CONCORD-25, CONCORD-65, CONCORD-73, CONCORD-76 | `backend/tests/test_permissions.py`, `backend/tests/test_realtime_gateway.py`; path/route attacks in PR #24. | Existing roles covered; route inventory/hostile edits in PR #24 await merge. |
| NFR-SEC4 | CONCORD-49, CONCORD-74 | `backend/tests/test_agent_runner.py`; adversarial extension pending. | Project-scoped tool access covered; additional abuse scenarios pending. |
| NFR-ORG-ENV1 | CONCORD-22, CONCORD-70 | `.github/workflows/ci.yml` e2e job; `scripts/pilot.sh` stack check. | Compose stack built and pilot/e2e passed in PR #24 CI. |
| NFR-ORG-ENV2 | CONCORD-35 | `worker/tests/test_worker_execution.py`; sandbox Dockerfile inspection. | Language image builds/execution passed in worker CI. |
| NFR-ORG-ENV3 | CONCORD-21, CONCORD-22 | `backend/tests/test_migrations_enums.py`; Compose image/persistence inspection. | Image/persistence configuration inspected; migration test covered. |
| NFR-ORG-OP1 | CONCORD-54 | Basic `/healthz` exists; dependency-check tests pending. | Partial: basic health exists; dependency/queue-depth health extension pending. |
| NFR-ORG-OP2 | CONCORD-32 | `backend/tests/test_checkpoint.py`; shutdown persistence pilot check pending. | Checkpoint logic covered; direct graceful-shutdown persistence check pending. |
| NFR-ORG-OP3 | CONCORD-41, CONCORD-61 | `backend/tests/test_execution_state_machine.py` checks the model; persisted event-log tests pending under CONCORD-41; unverified on main. | Pending implementation/verification: no append-only execution-event log proven on main. |
| NFR-ORG-DEV1 | CONCORD-22, CONCORD-56, CONCORD-72 | `.github/workflows/ci.yml`; PR #24 backend/worker/frontend/e2e passed; report/audit extension pending. | Backend, worker, frontend and e2e PR #24 checks passed; report/audit extension pending. |
| NFR-ORG-DEV2 | CONCORD-72, CONCORD-77 | CI `pytest --cov=app` report; main's gate is 78%; required 80% gate pending. | 80% gate pending: existing CI floor is 78%; passing it is not proof of this target. |
| NFR-ORG-DEV3 | CONCORD-58, CONCORD-68, CONCORD-78 | Jira/PR audit; final dashboard and sprint closeout pending; no automated code test. | Jira/PR records exist; final dashboard and sprint closeout pending. |
| NFR-EXT-REG1 | CONCORD-21, CONCORD-70 | TLS deployment check pending; `backend/tests/test_migrations_enums.py` plus account-schema review. | Partial: account-schema review possible; production HTTPS/WSS validation pending. |
| NFR-EXT-REG2 | CONCORD-24, CONCORD-73 | `jwt_expire_minutes=1440` configuration; `backend/tests/test_gateway_authorization.py`; expired-token attack in PR #24. | 24-hour setting inspected; expiry rejection in PR #24 awaits merge. |
| NFR-EXT-ETH1 | CONCORD-49, CONCORD-50, CONCORD-74 | `backend/tests/test_agent_runner.py`, `frontend/src/components/AssistantPanel.test.tsx`, `backend/tests/test_realtime_gateway.py`. | Proposal-only agent and human acceptance checks covered. |
| NFR-EXT-ETH2 | CONCORD-50, CONCORD-51, CONCORD-64 | `backend/tests/test_assistant_api.py`, `backend/tests/test_file_changes.py`; step-history extension pending. | Request/change history covered; per-tool step audit extension pending. |
| NFR-EXT-LEG-ACC1 | CONCORD-38, CONCORD-39, CONCORD-41 | `worker/tests/test_worker_execution.py`, `backend/tests/test_metrics.py`; CPU/peak-memory measurements unverified. | Partial: execution timing/output records covered; CPU time and peak memory unverified. |
| NFR-EXT-LEG-ACC2 | CONCORD-51 | `backend/tests/test_assistant_api.py`, `backend/tests/test_llm_adapters.py`. | Provider token counts and request persistence covered. |
| NFR-EXT-LEG-SEC1 | CONCORD-36, CONCORD-62 | `worker/tests/test_sandbox_security.py`; real-container network isolation checks. | Network isolation checks passed in worker CI. |
| NFR-EXT-LEG-SEC2 | CONCORD-36 | `worker/tests/test_sandbox_security.py`; configured PID-limit and real-container checks. | Configured process limit supported by worker security checks. |

## Recorded pilot evidence and remaining measurements

The existing repository report records an Edit latency check on 2026-10-15 on a
local Docker stack: three users, 300/300 deliveries, p50 1.5 ms and p99 8.4 ms.
This is historical three-client evidence; it does not establish NFR-P1's
ten-editor target. Repeat the prescribed load test and retain its raw output
before marking NFR-P1 or NFR-P2 met.

The existing Debug together pilot records shared paused state, locals and
late-join recovery. It verifies FR-17/FR-18 behaviour, not the NFR-P3 latency
threshold. Measure command-to-pause arrival on the observing client separately.

For NFR-P4, capture browser frame times during representative typing and report
the frame distribution against 16.6 ms. For NFR-D1, define scheduled laboratory
hours, retain timestamped health observations and calculate successful monitored
time divided by scheduled monitored time; the target is at least 99.5%.
For NFR-ORG-OP3, exercise queued, running, cancelled and swept executions and
verify one ordered persisted event for each accepted transition after CONCORD-41
merges. These three requirements remain explicitly unverified.

Load, worker-kill, five-request burst and debug-step experiments need their
actual environments, revision IDs, commands and outputs attached to the
corresponding Jira work. This report does not infer results for pending runs.

## Maintenance example: multiple files per project

The shared editor evolved from one file to a project file tree. The database
already scoped paths by project and executions by file, so adding file selection
did not require replacing the persistence model. Rooms are scoped by project and
file; selecting a tab connects to that file's collaboration socket.

FR-07 is checked by `backend/tests/test_files.py`,
`backend/tests/test_folders.py` and `frontend/src/components/FileTree.test.tsx`.
FR-25 is checked by `backend/tests/test_versions.py` and
`frontend/src/components/HistoryModal.test.tsx`. The remaining individual
file-deletion gap is recorded in the mapping rather than marked verified.

## Defect verification

Path traversal and absolute paths previously passed schema validation.
PR #24 adds a shared path validator and create/rename rejection tests
(CONCORD-65). Repeated bad passwords previously had no login throttle.
The same PR adds Redis-backed counters, a lockout response and deterministic
failure/reset/Redis-outage tests (CONCORD-66). Its adversarial suite checks
tampered/expired/missing tokens, protected project routes, Viewer WebSocket
writes, foreign-project files, traversal and oversized input (CONCORD-73).

These fixes remain In Progress until the reviewed PR is merged. Later
verification should cite the merged revision and preserve the distinction
between regression tests, measured targets and checks still pending.
