# Concord — System Architecture

## High-level diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                         Browser (React)                          │
│  ┌─────────────┐   ┌─────────────┐   ┌──────────────────────┐  │
│  │ Monaco Edit │◄──┤  y-monaco   │◄──┤ Yjs client (CRDT)    │  │
│  └─────────────┘   └─────────────┘   └──────────────────────┘  │
│         │                                   │                    │
│         │ debug controls                    │ WebSocket         │
│         ▼                                   ▼                    │
│  ┌──────────────────┐               ┌──────────────────┐         │
│  │  Debug UI (UI)   │               │  Awareness/      │         │
│  │                  │               │  presence        │         │
│  └──────────────────┘               └──────────────────┘         │
└────────────────────┬──────────────────────────┬─────────────────┘
                     │                          │
        HTTPS REST   │                          │  WSS
                     ▼                          ▼
        ┌────────────────────────────────────────────┐
        │     Backend (FastAPI) + Collab Gateway     │
        │                                            │
        │  • Auth (JWT) + REST CRUD                   │
        │  • pycrdt Doc registry (per session:file)  │
        │  • Awareness registry                       │
        │  • Execution Manager → enqueue to Redis    │
        │  • Debug Manager → DAP adapter             │
        │  • Snapshot/Version writers                 │
        └────┬─────────────────────┬──────────────┬──┘
             │                     │              │
             ▼                     ▼              ▼
        ┌────────┐            ┌─────────┐    ┌──────────┐
        │ Postgres│            │  Redis  │    │ Workers  │
        │  (truth)│            │ (queue  │    │ (RQ)     │
        │         │            │  + pub) │    │          │
        └────────┘            └────┬────┘    └────┬─────┘
                                   │              │
                                   │ pub/sub      │ spawns sibling containers
                                   │              │ (Docker socket mounted)
                                   ▼              ▼
                            ┌──────────────────────────────┐
                            │  Sandbox containers          │
                            │  python:3.12-slim            │
                            │  - network none              │
                            │  - mem_limit 128m            │
                            │  - pids_limit 64             │
                            │  - wall-clock timeout        │
                            │  - debugpy (for debug runs)  │
                            └──────────────────────────────┘
```

## Key design decisions

1. **CRDT (Yjs / pycrdt) for live editing, not OT.** Convergence is mathematically guaranteed
   by the library; we only own room registry, awareness, and persistence.
2. **Postgres is NOT the source of truth for live document state.** Yjs keeps the authoritative
   state in memory (server + clients). Postgres only gets periodic checkpoints (`document_versions`)
   and explicit snapshots — this keeps per-keystroke latency out of the database.
3. **Workers use sibling Docker containers, not Docker-in-Docker.** The worker container has
   `/var/run/docker.sock` mounted so it can `docker run` siblings — faster, simpler than DinD.
4. **Single-driver debugging.** Only one user controls stepping; others receive the read-only
   debug state (paused line, call stack, variables) over the same WebSocket fanout.
5. **Permissions are role-ranked and enforced everywhere.** REST routes, WebSocket messages, and
   worker entry points all call `require_role`. No trust of raw IDs.

## Data flow: an AI assistant request

1. User A picks a mode in the AI panel and sends a prompt. Frontend POSTs
   `/projects/{id}/assist` with `prompt`, `mode`, the open `file_id` and recent `history`.
2. Backend checks membership and the per-project rate limit, then picks a provider:
   the project's own key if configured, else the server's `MISTRAL_API_KEY`
   (`app/llm/service.py`).
3. The agent loop (`app/agent/runner.py`) sends the conversation plus tool
   definitions to Mistral. While the model asks for tools, the backend runs them
   and returns the results, up to `AI_AGENT_MAX_STEPS` turns:
   `list_files`, `read_file` (live CRDT doc if the file is open, else last
   checkpoint), `get_last_execution` (stdout/stderr of the last run),
   `check_syntax`, `propose_edit`.
4. `propose_edit` writes nothing: it checks that the text to replace exists
   exactly once and that the result still parses, and records a proposal.
5. Progress goes out on the project WebSocket while the agent runs:
   `AI_STARTED`, one `AI_STEP` per tool call, and `AI_FINISHED` (always, even on
   errors). The requester's panel matches them by `request_id` and shows the steps
   live; other members see "alice is asking the AI (fix, main.py)". No prompts,
   file contents or answers are broadcast. The REST response stays the source of
   truth, so a dropped socket only loses the live view.
6. The request is logged to `ai_assistant_requests` (tokens, latency, errors) and
   the answer, the tool steps and the proposal are returned.
7. The editor shows the proposal; an editor+ clicking Accept replaces the span on the
   shared `Y.Text` in one transaction, which syncs to everyone like any edit.

## Data flow: a code execution

1. User A clicks Run. Frontend POSTs `/sessions/{id}/run` with file_id.
2. Backend creates an `executions` row (status=QUEUED) and enqueues an RQ job.
3. A worker pulls the job, sets status=STARTING, then RUNNING.
4. Worker launches the sandbox container with `docker run` (network none, mem/cpu/pids limits).
5. Container runs the user's script. Worker captures stdout/stderr and exit code.
6. Worker updates the `executions` row (COMPLETED/FAILED/TIMEOUT) and publishes a result message
   to Redis pub/sub on `exec:{session_id}`.
7. The collab gateway, subscribed to that channel, forwards the result to every connected client
   in the session's room.
8. All members see the live output panel update simultaneously.
