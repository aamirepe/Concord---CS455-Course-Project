# Concord - Requirements Specification

Identifiers match Deliverable 1. Jira keys link implementation and verification
work. A Done issue alone does not prove that a measured target is met.

## Stakeholders

- Students need a shared environment for assignments and demonstrations.
- Instructors and teaching assistants observe code, runs and debugging.
- Operators deploy the system, monitor health and bound resource use.

## Functional requirements

All requirements are mandatory except FR-23 and FR-26. Evidence paths are
relative to the repository root. Pending tests are not evidence already on main.

| ID | Requirement and acceptance criterion | Jira tasks | Tests and checks |
|---|---|---|---|
| FR-01 | Register/authenticate accounts, store bcrypt hashes, and issue HS256 JWTs for REST and WebSockets. | CONCORD-23, CONCORD-24, CONCORD-66, CONCORD-73 | `backend/tests/test_auth.py`, `backend/tests/test_security.py`, `backend/tests/test_gateway_authorization.py`, `backend/tests/test_adversarial_api.py`. Verified: auth, token rejection and login throttling are covered by merged tests. |
| FR-02 | Creating a project makes its creator the Owner. | CONCORD-27 | `frontend/src/pages/DashboardPage.test.tsx`; pilot project creation check. |
| FR-03 | Editor or higher can issue seven-day invitations with a role no higher than their own. | CONCORD-29 | `backend/tests/test_invitations.py`. |
| FR-04 | Non-members request access; Owners approve with a role or reject; duplicate pending requests are refused. | CONCORD-29 | `backend/tests/test_join_requests.py`. |
| FR-05 | Owners add, remove and change members; their own role cannot be changed or removed. | CONCORD-26 | `backend/tests/test_members.py`. |
| FR-06 | Enforce Viewer, Editor, Debugger and Owner rights on REST and every WebSocket edit. | CONCORD-25, CONCORD-73, CONCORD-76 | `backend/tests/test_permissions.py`, `backend/tests/test_project_access_permissions.py`, `backend/tests/test_gateway_authorization.py`, `backend/tests/test_adversarial_api.py`, `e2e/test_execution.py::test_a_viewer_cannot_edit_or_run`. Verified for the tested REST, WebSocket, route-inventory and browser Viewer scenarios. |
| FR-07 | Members browse the tree; Editor or higher creates, renames, moves and deletes files/folders. | CONCORD-28, CONCORD-65 | `backend/tests/test_files.py`, `backend/tests/test_folders.py`, `frontend/src/components/FileTree.test.tsx`. Partial: create, rename, move, folder delete and path validation are covered; individual file deletion still has no route. |
| FR-08 | Concurrent CRDT edits converge without loss regardless of arrival order. | CONCORD-30, CONCORD-31, CONCORD-69 | `backend/tests/test_concurrent_edits.py`, `backend/tests/test_realtime_gateway.py`, `backend/tests/test_gateway_convergence.py`. Verified for tested orderings and 10/25 concurrent WebSocket clients, with matching text and no lost or duplicated edits. |
| FR-09 | Show collaborators' live cursor positions, names, colours and selections. | CONCORD-33 | `backend/tests/test_awareness.py`, `frontend/src/components/Editor.test.tsx`; two-browser pilot check. |
| FR-10 | Editor or higher runs a file through QUEUED, STARTING, optional COMPILING, RUNNING and one terminal state; illegal transitions are refused. | CONCORD-38, CONCORD-61 | `backend/tests/test_execution_state_machine.py`, `backend/tests/test_execution_events.py`, `worker/tests/test_worker_execution.py`. Verified: accepted transitions are checked atomically and persisted with their events. |
| FR-11 | Run Python/JavaScript, compile C++ first, report compilation failure, and accept stdin. | CONCORD-35, CONCORD-38, CONCORD-73 | `worker/tests/test_worker_execution.py`, `backend/tests/test_run_input.py`, `backend/tests/test_adversarial_api.py`. Verified: language execution, compilation and stdin boundary checks are covered. |
| FR-12 | Broadcast every status change and final stdout, stderr, exit code and duration; persist results. | CONCORD-39, CONCORD-41, CONCORD-67, CONCORD-76 | `e2e/test_execution.py`, `backend/tests/test_execution_events.py`, `frontend/src/components/ConsolePanel.test.tsx`; `scripts/experiments/e1_lifecycle.py`. Verified for browser shared status/output and ordered persisted events. The experiment script has simulated-service checks, not a recorded live lifecycle measurement. |
| FR-13 | Editor or higher cancels queued/running work, stops its container and records CANCELLED. | CONCORD-40, CONCORD-60, CONCORD-75 | `worker/tests/test_worker_execution.py`, `worker/tests/test_cancel_running.py`, `e2e/test_execution.py`; `scripts/experiments/e2_cancel.py`. Verified for tested running/compiling cancellation and container removal. The experiment script's checks use simulated services. |
| FR-14 | Redis queues work for a bounded pool, one execution per worker at a time. | CONCORD-37, CONCORD-69 | `backend/tests/test_priority_queue.py`, `backend/tests/test_queue_atomic_claims.py`, `worker/tests/test_worker_execution.py`. Verified: bounded workers and concurrent claims are covered, with no duplicate or missing claims. |
| FR-15 | Allocate the next free slot by deterministic priority including role, ageing and compilation cost. | CONCORD-42, CONCORD-43, CONCORD-69 | `backend/tests/test_priority_queue.py`, `backend/tests/test_queue_atomic_claims.py`; `scripts/experiments/e3_contention.py`. Verified for priority ordering and atomic claims. The contention experiment has simulated-service checks, not a live result. |
| FR-16 | Sweep every 60 seconds, fail executions stale beyond five minutes, and signal cancellation. | CONCORD-40, CONCORD-75 | `backend/tests/test_orphan_sweep.py`, `backend/tests/test_failure_recovery.py`, `scripts/chaos.py`; [PR #23](https://github.com/aamirepe/Concord---CS455-Course-Project/pull/23). Verified for tested recovery paths; one reported worker-kill run recovered in 338.207 s, below 360 s. Active debugging uses its separate timeout and recovery grace. |
| FR-17 | Debugger/Owner starts Python debugging, sets breakpoints and controls continue/next/step-in/step-out through DAP. | CONCORD-44, CONCORD-45 | `backend/tests/test_dap_client.py`, `backend/tests/test_breakpoints.py`, `backend/tests/test_debug.py`, `worker/tests/test_debug_session.py`; pilot Debug together check. |
| FR-18 | One debug session per project; members share paused state/variables; only Debugger/Owner steps. | CONCORD-46, CONCORD-47, CONCORD-69, CONCORD-71 | `backend/tests/test_debug.py`, `backend/tests/test_debug_session.py`, `backend/tests/test_debug_single_slot.py`, `frontend/src/components/DebugPanel.test.tsx`. Verified for shared debug state and the slot claim: one of 12 concurrent starts succeeds and 11 are refused with 409. See NFR-P3 for latency. |
| FR-19 | Editor or higher creates/restores named project snapshots, taking a safety snapshot and broadcasting restored content. | CONCORD-52 | `backend/tests/test_snapshots.py`, `frontend/src/components/SnapshotsPanel.test.tsx`. |
| FR-20 | Provide Mistral/HuggingFace through one interface; only Owners configure providers and encrypted keys. | CONCORD-48 | `backend/tests/test_llm_adapters.py`, `backend/tests/test_assistant_api.py`. |
| FR-21 | Any member uses ask/explain/review/fix/generate; at most six steps/five project-scoped tools; explain/review cannot propose edits; human acceptance gates writes. | CONCORD-49, CONCORD-50, CONCORD-74 | `backend/tests/test_agent_runner.py`, `backend/tests/test_assistant_api.py`, `frontend/src/components/AssistantPanel.test.tsx`; adversarial extension pending. |
| FR-22 | Record AI mode, prompt, response, provider, model, tokens, latency and outcome. | CONCORD-51, CONCORD-64 | `backend/tests/test_assistant_api.py`; tool-step audit extension pending. |
| FR-23 | Display execution counts, last-hour runs and recent events on a metrics page. | CONCORD-55, CONCORD-63 | `backend/tests/test_metrics.py`; authenticated project scoping pending. |
| FR-24 | Limit AI requests per project to ten per 60-second Redis-backed window. | CONCORD-51 | `backend/tests/test_llm_manager.py`, `backend/tests/test_assistant_api.py`. |
| FR-25 | Keep file versions, show differences and allow Editor or higher to restore a version. | CONCORD-53 | `backend/tests/test_versions.py`, `backend/tests/test_file_changes.py`, `frontend/src/components/HistoryModal.test.tsx`. |
| FR-26 | Search text across all project files. | CONCORD-53 | `backend/tests/test_search.py`, `frontend/src/components/SearchPanel.test.tsx`. |

## Non-functional requirements

Mechanism tests do not establish a measured service-level target. Original
thresholds are preserved; remaining measurements are recorded explicitly.

| ID | Requirement and target | Jira tasks | Tests/checks and current status |
|---|---|---|---|
| NFR-U1 | Current Chrome, Firefox, Safari and Edge without plugins/installations. | CONCORD-34, CONCORD-56, CONCORD-76 | `e2e/test_navigation.py`, `e2e/test_collaboration.py`, `e2e/test_execution.py`. Partial: browser workflow coverage is present; the complete Chrome/Firefox/Safari/Edge matrix is unverified. |
| NFR-U2 | Named, coloured cursors and selections. | CONCORD-33 | `backend/tests/test_awareness.py`, `frontend/src/components/Editor.test.tsx`; visual pilot check. |
| NFR-U3 | Readable compilation/runtime errors with line references. | CONCORD-38, CONCORD-34 | `worker/tests/test_worker_execution.py`, `frontend/src/components/ConsolePanel.test.tsx`; error-display pilot check. |
| NFR-U4 | Conventional IDE; a new student runs code within three minutes. | CONCORD-34, CONCORD-56 | `frontend/src/pages/SessionPage.test.tsx`, `e2e/test_navigation.py`; timed new-user study pending. |
| NFR-P1 | Ten editors: p99 propagation below 300 ms, target below 100 ms. | CONCORD-31, CONCORD-57, CONCORD-69 | `scripts/load_test.py`, `backend/tests/test_gateway_convergence.py`; [PR #30](https://github.com/aamirepe/Concord---CS455-Course-Project/pull/30). Met in five reported runs with ten editors: p99 23.9 to 198.7 ms, below 300 ms, with 18000/18000 deliveries each. The under-100-ms stretch goal was met in four runs. |
| NFR-P2 | QUEUED to STARTING below 1 second p95 for five simultaneous requests. | CONCORD-55, CONCORD-57, CONCORD-67 | `scripts/load_test.py`, `backend/tests/test_metrics.py`, `scripts/experiments/e4_burst.py`; [PR #30](https://github.com/aamirepe/Concord---CS455-Course-Project/pull/30). Not met: five simultaneous requests on two workers gave queue-wait p95 1.23 to 1.63 s in all five runs, above the 1 s target. |
| NFR-P3 | Debug steps visible to all collaborators within 150 ms. | CONCORD-46, CONCORD-67, CONCORD-71 | `backend/tests/test_debug_session.py`, `scripts/experiments/e5_debug_step.py`; [PR #28](https://github.com/aamirepe/Concord---CS455-Course-Project/pull/28). Partial: 120 steps had median 142.974 ms and p95 162.133 ms. The median is below 150 ms, but p95 and some individual steps exceed it. These timings predate the dependency refresh. |
| NFR-P4 | Typing user's editor renders at 60 FPS. | CONCORD-34 | `frontend/src/components/Editor.test.tsx` verifies behaviour only; browser frame-time profile at or below 16.6 ms pending. |
| NFR-S1 | 128 MB sandbox memory limit; terminate exhaustion. | CONCORD-36 | `worker/tests/test_sandbox_security.py`; real-container resource checks. Memory-limit and isolation mechanisms are covered by the Linux worker CI suite. |
| NFR-S2 | Sandbox CPU limit 0.5 CPU. | CONCORD-36 | `worker/tests/test_sandbox_security.py`; quota configuration and real-container inspection. |
| NFR-S3 | Production bundle at most 2.5 MB compressed. | CONCORD-34, CONCORD-72 | Frontend build in `.github/workflows/ci.yml`; compressed-size measurement pending. |
| NFR-S4 | Periodically prune stored file versions. | CONCORD-53 | `backend/tests/test_version_pruning.py`. |
| NFR-D1 | Availability at least 99.5% during scheduled laboratory hours. | CONCORD-54, CONCORD-70 | `backend/tests/test_health.py`, `docker-compose.prod.yml`, `.github/workflows/deploy.yml`, `docs/deployment.md`. Unverified: monitoring and deployment support exist, but no scheduled-laboratory uptime sample establishes 99.5% availability. |
| NFR-D2 | Backend crash loses at most five seconds of edits. | CONCORD-32, CONCORD-75 | `backend/tests/test_checkpoint.py`, `backend/tests/test_failure_recovery.py::test_backend_restart_restores_last_checkpoint`. Partial: restart restores the last checkpoint; a measured five-second crash-loss bound is still unverified. |
| NFR-D3 | CRDT replicas converge under every update ordering. | CONCORD-30, CONCORD-31, CONCORD-69 | `backend/tests/test_concurrent_edits.py`, `backend/tests/test_realtime_gateway.py`, `backend/tests/test_gateway_convergence.py`. Verified for tested update orderings and 10/25 concurrent clients through the real gateway. |
| NFR-D4 | Orphaned execution fails within six minutes of start. | CONCORD-40, CONCORD-75 | `backend/tests/test_orphan_sweep.py`, `backend/tests/test_failure_recovery.py`, `scripts/chaos.py`; [PR #23](https://github.com/aamirepe/Concord---CS455-Course-Project/pull/23). Met in one reported worker-kill run: FAILED after 338.207 s, below 360 s, with a sweeper event and container cleanup. Active debugging uses its separate timeout. |
| NFR-D5 | Give free slots to highest-priority waiting work; no duplicate slot assignment. | CONCORD-42, CONCORD-43, CONCORD-69 | `backend/tests/test_priority_queue.py`, `backend/tests/test_queue_atomic_claims.py`, `backend/tests/test_debug_single_slot.py`. Verified for priority logic and tested concurrent claims, with no double or missed allocation. Existing running work is not preempted. |
| NFR-SEC1 | UID 10001, no network, read-only root/workspace, all capabilities dropped. | CONCORD-36, CONCORD-62 | `worker/tests/test_sandbox_security.py`, `worker/tests/test_worker_workspace.py`, `backend/app/services/sandbox.py`, `backend/app/debug/docker_adapter.py`. Verified for tested run, build, validation and debug containers, including zero capabilities and no-new-privileges. |
| NFR-SEC2 | Bcrypt password hashes only; never log passwords. | CONCORD-23, CONCORD-66 | `backend/tests/test_auth.py`, `backend/tests/test_adversarial_api.py`; auth/logging source inspection. Hashing, login and throttling checks are covered; tests alone do not establish the absence of password logging. |
| NFR-SEC3 | Server enforces Viewer limits on edits, runs and settings. | CONCORD-25, CONCORD-65, CONCORD-73, CONCORD-76 | `backend/tests/test_permissions.py`, `backend/tests/test_realtime_gateway.py`, `backend/tests/test_adversarial_api.py`, `e2e/test_execution.py::test_a_viewer_cannot_edit_or_run`. Verified for tested server-side role checks, path/route attacks and browser Viewer restrictions. |
| NFR-SEC4 | AI tools access only the requesting project's files. | CONCORD-49, CONCORD-74 | `backend/tests/test_agent_runner.py`; adversarial extension pending. |
| NFR-ORG-ENV1 | Full system containerized with Docker Compose. | CONCORD-22, CONCORD-70 | `docker-compose.prod.yml`, `.github/workflows/deploy.yml`, `docs/deployment.md`, `.github/workflows/ci.yml`; `scripts/pilot.sh` stack check. Compose and production deployment configuration are merged; Linux CI verifies the local stack, not a remote production rollout. |
| NFR-ORG-ENV2 | Python 3.12, Node.js 20 and C++17/g++ on Ubuntu 22.04 images. | CONCORD-35 | `worker/tests/test_worker_execution.py`; sandbox Dockerfile inspection. |
| NFR-ORG-ENV3 | PostgreSQL 16 and Redis 7 with append-only persistence. | CONCORD-21, CONCORD-22 | `backend/tests/test_migrations_enums.py`; Compose image/persistence inspection. |
| NFR-ORG-OP1 | Health endpoint reports PostgreSQL, Redis and queue depth. | CONCORD-54 | `backend/tests/test_health.py`, `backend/app/main.py`. Verified: database, Redis and queue-depth checks cover degraded/unavailable responses and bounded dependency timeouts. |
| NFR-ORG-OP2 | Graceful shutdown checkpoints modified documents before disconnecting. | CONCORD-32 | `backend/tests/test_checkpoint.py`, `backend/tests/test_failure_recovery.py::test_backend_restart_restores_last_checkpoint`. Partial: checkpoint persistence and restart restoration are covered; direct graceful-shutdown persistence remains unverified. |
| NFR-ORG-OP3 | Append-only execution state-change event log. | CONCORD-41, CONCORD-61 | `backend/tests/test_execution_events.py`, `backend/tests/test_failure_recovery.py`. Verified: accepted transitions append ordered events; status and event commit or roll back together. |
| NFR-ORG-DEV1 | Every PR runs lint, types, security, unit tests, frontend build and e2e. | CONCORD-22, CONCORD-56, CONCORD-72 | `.github/workflows/ci.yml`; Linux main CI at `575df5c`. Verified: lint, types, security, tests, build and e2e pass; JUnit/coverage artifacts and dependency audit reports are uploaded. |
| NFR-ORG-DEV2 | Backend line coverage at least 80%. | CONCORD-72, CONCORD-77 | CI `pytest --cov=app` report; main's gate is 78%; required 80% gate pending. |
| NFR-ORG-DEV3 | Jira estimates and Definition of Done track work. | CONCORD-58, CONCORD-78 | Jira/PR audit; final dashboard and sprint closeout pending; no automated code test. |
| NFR-EXT-REG1 | HTTPS/WSS externally; no personal data beyond username/email. | CONCORD-21, CONCORD-70 | `docker-compose.prod.yml`, `.github/workflows/deploy.yml`, `docs/deployment.md`, `backend/tests/test_migrations_enums.py`; account-schema review. Partial: TLS deployment configuration exists; production HTTPS/WSS verification remains unrecorded. |
| NFR-EXT-REG2 | JWT expiry after 24 hours. | CONCORD-24, CONCORD-73 | `backend/tests/test_security.py::test_expired_token_is_rejected`, `backend/tests/test_adversarial_api.py`; `jwt_expire_minutes=1440` configuration. Verified: the 24-hour expiry setting is present and expired tokens are rejected. |
| NFR-EXT-ETH1 | AI writes require explicit human acceptance. | CONCORD-49, CONCORD-50, CONCORD-74 | `backend/tests/test_agent_runner.py`, `frontend/src/components/AssistantPanel.test.tsx`, `backend/tests/test_realtime_gateway.py`. |
| NFR-EXT-ETH2 | Retain AI history and attribute accepted changes to the accepting member. | CONCORD-50, CONCORD-51, CONCORD-64 | `backend/tests/test_assistant_api.py`, `backend/tests/test_file_changes.py`; step-history extension pending. |
| NFR-EXT-LEG-ACC1 | Record queue/start/finish times, exit code and worker; CPU/peak memory need added instrumentation. | CONCORD-38, CONCORD-39, CONCORD-41 | `worker/tests/test_worker_execution.py`, `backend/tests/test_metrics.py`; CPU/peak-memory measurements unverified. |
| NFR-EXT-LEG-ACC2 | Record AI tokens per request to bound API cost. | CONCORD-51 | `backend/tests/test_assistant_api.py`, `backend/tests/test_llm_adapters.py`. |
| NFR-EXT-LEG-SEC1 | No sandbox network access to host, metadata services or internet. | CONCORD-36, CONCORD-62 | `worker/tests/test_sandbox_security.py`, `backend/app/services/sandbox.py`, `backend/app/debug/docker_adapter.py`. Network isolation and capability/no-new-privileges checks are covered by Linux worker CI. |
| NFR-EXT-LEG-SEC2 | Limit sandboxes to 64 processes. | CONCORD-36 | `worker/tests/test_sandbox_security.py`; configured PID-limit and real-container checks. |

## Evidence status

This is an evidence snapshot of main at `575df5c` (2026-10-27). It includes
merged PRs #21, #22, #23, #24, #25, #27, #28, #29 and #30. Measured results cite
the PR that recorded them; those results apply to the reported environments.

CONCORD-68 documents this mapping. Measurements are attributed to their original
runs, and unmeasured targets remain explicit. See [the verification report](vv-report.md)
for commands, sources and limits.
