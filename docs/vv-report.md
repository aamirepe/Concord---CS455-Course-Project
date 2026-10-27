# Concord - Verification and Validation Report

This report uses the same 26 functional and 38 non-functional identifiers as
[the requirements specification](requirements.md). Each row identifies the
test or pilot check, its Jira work and the limits of the available evidence.

## Evidence baseline

This is an evidence snapshot of main at `575df5c` (2026-10-27). It includes
merged PRs #21, #22, #23, #24, #25, #27, #28, #29 and #30. Measured results cite
the PR that recorded them; those results apply to the reported environments.

[Linux CI on this main revision](https://github.com/aamirepe/Concord---CS455-Course-Project/actions/runs/40206036253)
passed. Functional tests establish the scenarios they exercise; they do not
replace performance, availability or browser compatibility measurements.

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
`npm run test:ci` and `npm run build`. For the Docker stack and seeded
multi-user checks, run `scripts/pilot.sh up`, then `scripts/pilot.sh check`.
The e2e job in `.github/workflows/ci.yml` documents the browser-test container.

Linux CI is the reference for sandbox-workspace tests that require `os.chown`.

## Functional requirement mapping

| Requirement | Jira tasks | Verification test or pilot check | Evidence status |
|---|---|---|---|
| FR-01 | CONCORD-23, CONCORD-24, CONCORD-66, CONCORD-73 | `backend/tests/test_auth.py`, `backend/tests/test_security.py`, `backend/tests/test_gateway_authorization.py`, `backend/tests/test_adversarial_api.py`. | Verified: auth, token rejection and login throttling are covered by merged tests. |
| FR-02 | CONCORD-27 | `frontend/src/pages/DashboardPage.test.tsx`; pilot project creation check. | Functional evidence exists; creation/ownership checked by pilot and client tests. |
| FR-03 | CONCORD-29 | `backend/tests/test_invitations.py`. | Invitation role/expiry checks covered by CI. |
| FR-04 | CONCORD-29 | `backend/tests/test_join_requests.py`. | Approve/reject/duplicate-request checks covered by CI. |
| FR-05 | CONCORD-26 | `backend/tests/test_members.py`. | Member-management and protected-owner checks covered by CI. |
| FR-06 | CONCORD-25, CONCORD-73, CONCORD-76 | `backend/tests/test_permissions.py`, `backend/tests/test_project_access_permissions.py`, `backend/tests/test_gateway_authorization.py`, `backend/tests/test_adversarial_api.py`, `e2e/test_execution.py::test_a_viewer_cannot_edit_or_run`. | Verified for the tested REST, WebSocket, route-inventory and browser Viewer scenarios. |
| FR-07 | CONCORD-28, CONCORD-65 | `backend/tests/test_files.py`, `backend/tests/test_folders.py`, `frontend/src/components/FileTree.test.tsx`. | Partial: create, rename, move, folder delete and path validation are covered; individual file deletion still has no route. |
| FR-08 | CONCORD-30, CONCORD-31, CONCORD-69 | `backend/tests/test_concurrent_edits.py`, `backend/tests/test_realtime_gateway.py`, `backend/tests/test_gateway_convergence.py`. | Verified for tested orderings and 10/25 concurrent WebSocket clients, with matching text and no lost or duplicated edits. |
| FR-09 | CONCORD-33 | `backend/tests/test_awareness.py`, `frontend/src/components/Editor.test.tsx`; two-browser pilot check. | Awareness behaviour covered; visual multi-client pilot evidence recorded. |
| FR-10 | CONCORD-38, CONCORD-61 | `backend/tests/test_execution_state_machine.py`, `backend/tests/test_execution_events.py`, `worker/tests/test_worker_execution.py`. | Verified: accepted transitions are checked atomically and persisted with their events. |
| FR-11 | CONCORD-35, CONCORD-38, CONCORD-73 | `worker/tests/test_worker_execution.py`, `backend/tests/test_run_input.py`, `backend/tests/test_adversarial_api.py`. | Verified: language execution, compilation and stdin boundary checks are covered. |
| FR-12 | CONCORD-39, CONCORD-41, CONCORD-67, CONCORD-76 | `e2e/test_execution.py`, `backend/tests/test_execution_events.py`, `frontend/src/components/ConsolePanel.test.tsx`; `scripts/experiments/e1_lifecycle.py`. | Verified for browser shared status/output and ordered persisted events. The experiment script has simulated-service checks, not a recorded live lifecycle measurement. |
| FR-13 | CONCORD-40, CONCORD-60, CONCORD-75 | `worker/tests/test_worker_execution.py`, `worker/tests/test_cancel_running.py`, `e2e/test_execution.py`; `scripts/experiments/e2_cancel.py`. | Verified for tested running/compiling cancellation and container removal. The experiment script's checks use simulated services. |
| FR-14 | CONCORD-37, CONCORD-69 | `backend/tests/test_priority_queue.py`, `backend/tests/test_queue_atomic_claims.py`, `worker/tests/test_worker_execution.py`. | Verified: bounded workers and concurrent claims are covered, with no duplicate or missing claims. |
| FR-15 | CONCORD-42, CONCORD-43, CONCORD-69 | `backend/tests/test_priority_queue.py`, `backend/tests/test_queue_atomic_claims.py`; `scripts/experiments/e3_contention.py`. | Verified for priority ordering and atomic claims. The contention experiment has simulated-service checks, not a live result. |
| FR-16 | CONCORD-40, CONCORD-75 | `backend/tests/test_orphan_sweep.py`, `backend/tests/test_failure_recovery.py`, `scripts/chaos.py`; [PR #23](https://github.com/aamirepe/Concord---CS455-Course-Project/pull/23). | Verified for tested recovery paths; one reported worker-kill run recovered in 338.207 s, below 360 s. Active debugging uses its separate timeout and recovery grace. |
| FR-17 | CONCORD-44, CONCORD-45 | `backend/tests/test_dap_client.py`, `backend/tests/test_breakpoints.py`, `backend/tests/test_debug.py`, `worker/tests/test_debug_session.py`; pilot Debug together check. | DAP and real sandbox worker checks passed; pilot Debug together evidence recorded. |
| FR-18 | CONCORD-46, CONCORD-47, CONCORD-69, CONCORD-71 | `backend/tests/test_debug.py`, `backend/tests/test_debug_session.py`, `backend/tests/test_debug_single_slot.py`, `frontend/src/components/DebugPanel.test.tsx`. | Verified for shared debug state and the slot claim: one of 12 concurrent starts succeeds and 11 are refused with 409. See NFR-P3 for latency. |
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
| NFR-U1 | CONCORD-34, CONCORD-56, CONCORD-76 | `e2e/test_navigation.py`, `e2e/test_collaboration.py`, `e2e/test_execution.py`. | Partial: browser workflow coverage is present; the complete Chrome/Firefox/Safari/Edge matrix is unverified. |
| NFR-U2 | CONCORD-33 | `backend/tests/test_awareness.py`, `frontend/src/components/Editor.test.tsx`; visual pilot check. | Behaviour covered; confirm colours/names/selections visually in pilot. |
| NFR-U3 | CONCORD-38, CONCORD-34 | `worker/tests/test_worker_execution.py`, `frontend/src/components/ConsolePanel.test.tsx`; error-display pilot check. | Error results/rendering covered; inspect representative line references in pilot. |
| NFR-U4 | CONCORD-34, CONCORD-56 | `frontend/src/pages/SessionPage.test.tsx`, `e2e/test_navigation.py`; timed new-user study pending. | Layout covered; three-minute novice usability target unmeasured. |
| NFR-P1 | CONCORD-31, CONCORD-57, CONCORD-69 | `scripts/load_test.py`, `backend/tests/test_gateway_convergence.py`; [PR #30](https://github.com/aamirepe/Concord---CS455-Course-Project/pull/30). | Met in five reported runs with ten editors: p99 23.9 to 198.7 ms, below 300 ms, with 18000/18000 deliveries each. The under-100-ms stretch goal was met in four runs. |
| NFR-P2 | CONCORD-55, CONCORD-57, CONCORD-67 | `scripts/load_test.py`, `backend/tests/test_metrics.py`, `scripts/experiments/e4_burst.py`; [PR #30](https://github.com/aamirepe/Concord---CS455-Course-Project/pull/30). | Not met: five simultaneous requests on two workers gave queue-wait p95 1.23 to 1.63 s in all five runs, above the 1 s target. |
| NFR-P3 | CONCORD-46, CONCORD-67, CONCORD-71 | `backend/tests/test_debug_session.py`, `scripts/experiments/e5_debug_step.py`; [PR #28](https://github.com/aamirepe/Concord---CS455-Course-Project/pull/28). | Partial: 120 steps had median 142.974 ms and p95 162.133 ms. The median is below 150 ms, but p95 and some individual steps exceed it. These timings predate the dependency refresh. |
| NFR-P4 | CONCORD-34 | `frontend/src/components/Editor.test.tsx` verifies behaviour only; browser frame-time profile at or below 16.6 ms pending. | Unverified: no typing FPS/frame-time capture; unit tests do not measure rendering speed. |
| NFR-S1 | CONCORD-36 | `worker/tests/test_sandbox_security.py`; real-container resource checks. | Memory-limit and isolation mechanisms are covered by the Linux worker CI suite. |
| NFR-S2 | CONCORD-36 | `worker/tests/test_sandbox_security.py`; quota configuration and real-container inspection. | Configured quota supported by container checks; CPU scheduling performance is not inferred. |
| NFR-S3 | CONCORD-34, CONCORD-72 | Frontend build in `.github/workflows/ci.yml`; compressed-size measurement pending. | Build passed; compressed bundle threshold unmeasured. |
| NFR-S4 | CONCORD-53 | `backend/tests/test_version_pruning.py`. | Retention/pruning rules covered by unit/integration checks. |
| NFR-D1 | CONCORD-54, CONCORD-70 | `backend/tests/test_health.py`, `docker-compose.prod.yml`, `.github/workflows/deploy.yml`, `docs/deployment.md`. | Unverified: monitoring and deployment support exist, but no scheduled-laboratory uptime sample establishes 99.5% availability. |
| NFR-D2 | CONCORD-32, CONCORD-75 | `backend/tests/test_checkpoint.py`, `backend/tests/test_failure_recovery.py::test_backend_restart_restores_last_checkpoint`. | Partial: restart restores the last checkpoint; a measured five-second crash-loss bound is still unverified. |
| NFR-D3 | CONCORD-30, CONCORD-31, CONCORD-69 | `backend/tests/test_concurrent_edits.py`, `backend/tests/test_realtime_gateway.py`, `backend/tests/test_gateway_convergence.py`. | Verified for tested update orderings and 10/25 concurrent clients through the real gateway. |
| NFR-D4 | CONCORD-40, CONCORD-75 | `backend/tests/test_orphan_sweep.py`, `backend/tests/test_failure_recovery.py`, `scripts/chaos.py`; [PR #23](https://github.com/aamirepe/Concord---CS455-Course-Project/pull/23). | Met in one reported worker-kill run: FAILED after 338.207 s, below 360 s, with a sweeper event and container cleanup. Active debugging uses its separate timeout. |
| NFR-D5 | CONCORD-42, CONCORD-43, CONCORD-69 | `backend/tests/test_priority_queue.py`, `backend/tests/test_queue_atomic_claims.py`, `backend/tests/test_debug_single_slot.py`. | Verified for priority logic and tested concurrent claims, with no double or missed allocation. Existing running work is not preempted. |
| NFR-SEC1 | CONCORD-36, CONCORD-62 | `worker/tests/test_sandbox_security.py`, `worker/tests/test_worker_workspace.py`, `backend/app/services/sandbox.py`, `backend/app/debug/docker_adapter.py`. | Verified for tested run, build, validation and debug containers, including zero capabilities and no-new-privileges. |
| NFR-SEC2 | CONCORD-23, CONCORD-66 | `backend/tests/test_auth.py`, `backend/tests/test_adversarial_api.py`; auth/logging source inspection. | Hashing, login and throttling checks are covered; tests alone do not establish the absence of password logging. |
| NFR-SEC3 | CONCORD-25, CONCORD-65, CONCORD-73, CONCORD-76 | `backend/tests/test_permissions.py`, `backend/tests/test_realtime_gateway.py`, `backend/tests/test_adversarial_api.py`, `e2e/test_execution.py::test_a_viewer_cannot_edit_or_run`. | Verified for tested server-side role checks, path/route attacks and browser Viewer restrictions. |
| NFR-SEC4 | CONCORD-49, CONCORD-74 | `backend/tests/test_agent_runner.py`; adversarial extension pending. | Project-scoped tool access covered; additional abuse scenarios pending. |
| NFR-ORG-ENV1 | CONCORD-22, CONCORD-70 | `docker-compose.prod.yml`, `.github/workflows/deploy.yml`, `docs/deployment.md`, `.github/workflows/ci.yml`; `scripts/pilot.sh` stack check. | Compose and production deployment configuration are merged; Linux CI verifies the local stack, not a remote production rollout. |
| NFR-ORG-ENV2 | CONCORD-35 | `worker/tests/test_worker_execution.py`; sandbox Dockerfile inspection. | Language image builds/execution passed in worker CI. |
| NFR-ORG-ENV3 | CONCORD-21, CONCORD-22 | `backend/tests/test_migrations_enums.py`; Compose image/persistence inspection. | Image/persistence configuration inspected; migration test covered. |
| NFR-ORG-OP1 | CONCORD-54 | `backend/tests/test_health.py`, `backend/app/main.py`. | Verified: database, Redis and queue-depth checks cover degraded/unavailable responses and bounded dependency timeouts. |
| NFR-ORG-OP2 | CONCORD-32 | `backend/tests/test_checkpoint.py`, `backend/tests/test_failure_recovery.py::test_backend_restart_restores_last_checkpoint`. | Partial: checkpoint persistence and restart restoration are covered; direct graceful-shutdown persistence remains unverified. |
| NFR-ORG-OP3 | CONCORD-41, CONCORD-61 | `backend/tests/test_execution_events.py`, `backend/tests/test_failure_recovery.py`. | Verified: accepted transitions append ordered events; status and event commit or roll back together. |
| NFR-ORG-DEV1 | CONCORD-22, CONCORD-56, CONCORD-72 | `.github/workflows/ci.yml`; Linux main CI at `575df5c`. | Verified: lint, types, security, tests, build and e2e pass; JUnit/coverage artifacts and dependency audit reports are uploaded. |
| NFR-ORG-DEV2 | CONCORD-72, CONCORD-77 | CI `pytest --cov=app` report; main's gate is 78%; required 80% gate pending. | 80% gate pending: existing CI floor is 78%; passing it is not proof of this target. |
| NFR-ORG-DEV3 | CONCORD-58, CONCORD-78 | Jira/PR audit; final dashboard and sprint closeout pending; no automated code test. | Jira/PR records exist; final dashboard and sprint closeout pending. |
| NFR-EXT-REG1 | CONCORD-21, CONCORD-70 | `docker-compose.prod.yml`, `.github/workflows/deploy.yml`, `docs/deployment.md`, `backend/tests/test_migrations_enums.py`; account-schema review. | Partial: TLS deployment configuration exists; production HTTPS/WSS verification remains unrecorded. |
| NFR-EXT-REG2 | CONCORD-24, CONCORD-73 | `backend/tests/test_security.py::test_expired_token_is_rejected`, `backend/tests/test_adversarial_api.py`; `jwt_expire_minutes=1440` configuration. | Verified: the 24-hour expiry setting is present and expired tokens are rejected. |
| NFR-EXT-ETH1 | CONCORD-49, CONCORD-50, CONCORD-74 | `backend/tests/test_agent_runner.py`, `frontend/src/components/AssistantPanel.test.tsx`, `backend/tests/test_realtime_gateway.py`. | Proposal-only agent and human acceptance checks covered. |
| NFR-EXT-ETH2 | CONCORD-50, CONCORD-51, CONCORD-64 | `backend/tests/test_assistant_api.py`, `backend/tests/test_file_changes.py`; step-history extension pending. | Request/change history covered; per-tool step audit extension pending. |
| NFR-EXT-LEG-ACC1 | CONCORD-38, CONCORD-39, CONCORD-41 | `worker/tests/test_worker_execution.py`, `backend/tests/test_metrics.py`; CPU/peak-memory measurements unverified. | Partial: execution timing/output records covered; CPU time and peak memory unverified. |
| NFR-EXT-LEG-ACC2 | CONCORD-51 | `backend/tests/test_assistant_api.py`, `backend/tests/test_llm_adapters.py`. | Provider token counts and request persistence covered. |
| NFR-EXT-LEG-SEC1 | CONCORD-36, CONCORD-62 | `worker/tests/test_sandbox_security.py`, `backend/app/services/sandbox.py`, `backend/app/debug/docker_adapter.py`. | Network isolation and capability/no-new-privileges checks are covered by Linux worker CI. |
| NFR-EXT-LEG-SEC2 | CONCORD-36 | `worker/tests/test_sandbox_security.py`; configured PID-limit and real-container checks. | Configured process limit supported by worker security checks. |

## Recorded pilot evidence and remaining measurements

[PR #30](https://github.com/aamirepe/Concord---CS455-Course-Project/pull/30)
records five local Docker runs with ten editors and five simultaneous run
requests on two workers. Each run delivered 18000/18000 edits; edit p99 ranged
from 23.9 to 198.7 ms, meeting NFR-P1's 300 ms limit. The under-100-ms stretch
goal was met in four of five runs. Queue-wait p95 ranged from 1.23 to 1.63 s,
so NFR-P2's under-one-second target was not met in any run.

[PR #28](https://github.com/aamirepe/Concord---CS455-Course-Project/pull/28)
records 120 debug steps after optimisation: median 142.974 ms and p95 162.133 ms.
NFR-P3 is partial: the median is below 150 ms, but p95 and some steps exceed it.
These measurements predate that PR's dependency refresh.

[PR #23](https://github.com/aamirepe/Concord---CS455-Course-Project/pull/23)
records one isolated worker-kill recovery in 338.207 s, within NFR-D4's 360 s
limit, with a sweeper event and container removal.
`backend/tests/test_execution_events.py` verifies ordered events and atomic
status/event rollback for NFR-ORG-OP3. Checkpoint restoration is covered by
`backend/tests/test_failure_recovery.py::test_backend_restart_restores_last_checkpoint`;
this does not measure the five-second crash-loss bound.

The scripts `scripts/experiments/e1_lifecycle.py`,
`scripts/experiments/e2_cancel.py`, `scripts/experiments/e3_contention.py`,
`scripts/experiments/e4_burst.py` and `scripts/experiments/e5_debug_step.py`
are merged from #27. That PR reports simulated-service checks only, not live
pilot measurements. They provide tooling for repeating the relevant scenarios;
the live results above come from #23, #28 and #30.

NFR-P4 remains unverified without a typing frame-time profile against 16.6 ms.
NFR-D1 remains unverified without health observations over defined laboratory
hours and an availability calculation against 99.5%. Retain environments,
revision IDs, sample counts and raw observations with measured results.

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
PR #24 added a shared path validator and create/rename rejection tests
(CONCORD-65). Repeated bad passwords previously had no login throttle.
The same PR added Redis-backed counters, a lockout response and deterministic
failure/reset/Redis-outage tests (CONCORD-66). Its merged adversarial suite checks
tampered/expired/missing tokens, protected project routes, Viewer WebSocket
writes, foreign-project files, traversal and oversized input (CONCORD-73).

PR #24 merged these fixes into main. The current snapshot includes their
regression tests; measured targets and checks without recorded results retain
their separate status above.
