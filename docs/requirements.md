# Concord — Requirements Specification

## Stakeholders
- **Students (primary):** need a shared coding space for pair programming, lab assignments, and demos.
- **Instructors/TAs:** review student code live without taking over the editor.
- **Course staff:** deploy the platform, observe system health, run load tests.

## Functional requirements

| ID | Requirement | Acceptance criterion |
|---|---|---|
| FR1 | Users can sign up, log in, and obtain a JWT. | `POST /auth/signup` and `/auth/login` return 201/200 with a valid token. |
| FR2 | Users can create projects and open them for live collaboration. | `POST /projects` succeeds and members connect to `/ws/projects/{id}` and `/ws/projects/{id}/files/{file_id}`. |
| FR3 | Users can request to join an existing project by ID; the owner approves with a role. | `POST /projects/{id}/join-requests` records the request, `POST /projects/{id}/join-requests/{request_id}/approve` returns the membership and role, and `POST /projects/{id}/join` then returns the member's role. |
| FR4 | Edits propagate to all connected users in <300ms p99. | See §6.1 acceptance test. |
| FR5 | N concurrent random edits converge to identical state on all clients. | `backend/tests/test_concurrent_edits.py` passes for N≥10, 100 edits. |
| FR6 | Snapshots can be created and restored; restore rebroadcasts full state. | `POST /projects/{id}/snapshots` + `POST /projects/{id}/snapshots/{snapshot_id}/restore` succeed. |
| FR7 | Code runs in an isolated Docker container with resource limits. | `worker/tests/test_sandbox_security.py` passes (network, fork bomb, fs write). |
| FR8 | A user can set breakpoints, run, step, and inspect variables. | Manual demo + `PUT /projects/{id}/files/{file_id}/breakpoints` round-trip and `POST /projects/{id}/debug/start`. |
| FR9 | Debug state is broadcast to all session members in real time. | `exec_event` WS messages received by other clients. |
| FR10 | All mutating endpoints enforce role-based permissions. | `backend/tests/test_permissions.py`, `test_project_access_permissions.py` and `test_gateway_authorization.py` pass. |
| FR11 | Periodic CRDT checkpoints durably persist document state. | `document_versions` rows appear within 5s of activity. |
| FR12 | The system survives a worker crash mid-execution. | `backend/tests/test_orphan_sweep.py` passes; the worker-kill experiment is Deliverable 3. |
| FR13 | Members can ask a project-aware AI agent to explain, review, fix or write code; its edits are only proposals and need an editor+ to apply them. | `tests/test_agent_runner.py`, `tests/test_assistant_api.py`, `AssistantPanel.test.tsx` pass; `scripts/agent_smoke.py` passes against the real Mistral API. |

## Non-functional requirements

| ID | Requirement | Target | Verification |
|---|---|---|---|
| NFR1 | Edit propagation latency (intra-region) | <300ms p99 | pilot check "Edit latency" (`scripts/latency.py`); full load test in Deliverable 3 |
| NFR2 | Concurrent users per session | ≥10 without degradation | load test (Deliverable 3) |
| NFR3 | Execution queue wait time | <1s p95 under normal load | metrics page |
| NFR4 | Sandbox escape | None for the documented test set | `worker/tests/test_sandbox_security.py` |
| NFR5 | TLS for all external traffic | Required for grading demo | Verified at deployment (Deliverable 3) |
| NFR6 | Cold restart recovers session state | All files reconstructed within 5s | manual + checkpoint_loop |
| NFR7 | CI pipeline runs lint, type check, security, tests | Every PR | .github/workflows/ci.yml |
