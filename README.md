# Concord — Collaborative Code Execution & Debugging Platform

Concord is a browser-based IDE for teams. Several people edit the same files at the
same time, run the code in isolated Docker sandboxes, watch the output live, and ask a
project-aware AI assistant to explain or fix it. Every change syncs through CRDTs, so
nobody's edits get lost or duplicated.

## 🚀 Getting Started

### Prerequisites
- **Docker & Docker Compose v2**. This is all you need to run it.
  - Ubuntu/WSL2: `sudo apt-get install docker.io docker-compose-v2 docker-buildx`.
  - Avoid the snap package: its private `/tmp` breaks the sandbox mounts.
- **Node.js 20+** and **Python 3.12**, only for developing without Docker.

### Try it: local pilot (one command)

```bash
git clone https://github.com/aamirepe/Concord---CS455-Course-Project.git
cd Concord---CS455-Course-Project
scripts/pilot.sh up        # or: make pilot
```

This one command:
1. Checks Docker and the ports.
2. Creates `.env` with fresh secrets.
3. Builds and starts every service.
4. Seeds demo accounts and a demo project.
5. Runs 18 end-to-end checks against the real stack: co-editing and its latency, Save, history and snapshots, permissions, running Python (with input), debugging together, C++ and JavaScript, the sandbox limits, and the AI.

Then open **http://localhost:5173**. Every demo account's password is `pilot-pass-123`:

| User | Role in "Pilot demo" |
|---|---|
| alice | owner |
| bob | editor |
| carol | viewer |
| dave | debugger |
| erin | not a member (for the join-request flow) |

[docs/PILOT_TEST.md](docs/PILOT_TEST.md) walks a first-time tester through the
scenarios and explains how to report problems. Other commands:

| Command | What it does |
|---|---|
| `scripts/pilot.sh check` | re-run the automated checks |
| `scripts/pilot.sh status` | what's running |
| `scripts/pilot.sh logs [service]` | follow one service's logs |
| `scripts/pilot.sh collect` | save logs to share (API keys removed) |
| `scripts/pilot.sh stop` / `scripts/pilot.sh reset` | pause / delete all data |

### Manual setup

```bash
cp .env.example .env       # set SECRET_KEY, LLM_ENCRYPTION_KEY and optionally MISTRAL_API_KEY
docker compose build       # includes the three sandbox images
docker compose up -d postgres redis backend worker frontend
```

- **App:** http://localhost:5173
- **API docs (Swagger):** http://localhost:8000/docs

The backend applies database migrations (`alembic upgrade head`) on start.

---

## 🌟 Core Capabilities

### 🤝 Real-Time Collaboration (CRDTs)
Edits are **Yjs** updates in the browser and **pycrdt** on the server, sent over WebSockets.
- **Conflict-free editing:** simultaneous edits converge. There's no locking and no "file changed on disk".
- **Presence:** see who is in the project and in which file.
- **Live project events:** new and renamed files, and folder changes, show up for everyone instantly.
- **Durable:** documents are checkpointed to Postgres a few seconds after they change, and survive a backend restart.
- **Survives dropped connections:** if the connection drops (a sleeping laptop, a server restart), the editor says "Offline", keeps your edits and sends them when it reconnects. A status line shows "✓ All changes synced" once the server has everything.
- **Save (Ctrl+S):** edits save automatically, and **Save** makes a version on the spot.
- **History and snapshots:** each file's versions with a side-by-side diff and one-click restore; named snapshots of the whole project. Restores reach everyone's open editor.
- **Cursors:** everyone's cursor shows in their colour with their name.
- **Tabs, Ctrl+P and search:** open files as tabs (with who else is in each), jump to a file by name, and search every file, unsaved edits included.
- **Recent changes:** the sidebar lists the five most recently changed files with who changed them and by how many lines (`alice +12 −3`). Files that others changed since you last opened them get a ● in the file tree.

### 🛡️ Sandboxed Execution
Code never runs on the server itself. **Run** queues a job in Redis; a worker pool
picks it up and starts a fresh container per run.
- **Languages:** Python, C/C++ (compiled with g++) and JavaScript (Node 20).
- **Limits:** no network, a read-only root filesystem, a non-root user, and memory, CPU, process-count and wall-clock limits (`SANDBOX_*` in `.env`).
- **Live console:** one block per run (who ran it, status, output, exit code) for everyone in the project, with **Ask AI to fix** on failed runs.
- **Program input:** type what the program reads from stdin before pressing Run.

### 🔑 Roles and Access Control
| Role | Can |
|---|---|
| viewer | read code, watch edits and runs, use the AI assistant (read-only) |
| editor | + edit, run code, accept AI proposals |
| debugger | + set breakpoints, start and step the debugger |
| owner | + manage members and join requests |

Permissions are enforced on the server, not just in the UI. For example, the
collaboration gateway rejects edits from viewers.

To join a project, a user enters its ID and requests access; the owner approves (choosing
a role) or rejects.

### 🧠 AI Coding Assistant (Mistral)
- **An agent, not a single chat call:** it uses Mistral function calling to list files, read their live contents, look at the last run's output and syntax-check code before answering.
- **Modes:** Ask, Explain, Fix last error, Generate, Review.
- **Human in the loop:** a code change comes back as a proposal, shown side by side. An editor clicks **Accept** and it syncs through the CRDT like any other edit. Nothing is written server-side.
- **Starts with context:** the open file, your selection, and (when fixing) the last run go into the first message, so fixing a bug usually takes one model call. Answers are formatted, with copyable code blocks.
- **Live progress:** the requester sees each step as it happens ("read main.py", "checked last run…"). Other members see "alice is asking the AI (fix, main.py)" over the project WebSocket, but never the prompt or the answer.
- **Setup:** put a free key from [console.mistral.ai](https://console.mistral.ai) in `MISTRAL_API_KEY`, or let each project owner add one in the AI panel.
  - Keep `MISTRAL_MODEL=codestral-latest`: free trial keys can have no quota for `mistral-small`.
  - Check your key with `PYTHONPATH=backend python scripts/agent_smoke.py`.

### 🐞 Collaborative Debugger (Python)
- Click left of a line number to set a breakpoint, then press **Debug**.
- The program runs under debugpy inside the same sandbox (still no network). The worker talks to debugpy over the container's stdin/stdout.
- Everyone in the project sees the paused line, the call stack and the variables live, and who stepped last. Members with the debugger role step: continue (F5), over (F10), into (F11), out (Shift+F11).
- Someone who opens the project mid-session sees where it's paused.

---

## 🛠 Architecture Overview

```
Browser (React + Monaco + Yjs) ──REST──▶ FastAPI backend ──▶ PostgreSQL
        │  ▲                               │   │
        │  └──── WebSockets ◀──────────────┘   └──▶ Mistral API (AI agent)
        │        (CRDT sync, presence,           │
        │         run output, AI progress)       ▼
        └───────────────────────────────── Redis (job queue + pub/sub)
                                                 │
                                    Worker pool ─┴─▶ Docker sandbox per run
```

- **Frontend:** React, TypeScript, Vite, Monaco Editor, Yjs.
- **Backend:** FastAPI, SQLAlchemy (async), Alembic, JWT auth.
- **Collaboration gateway:** `pycrdt` rooms over WebSockets (`backend/app/realtime/`).
- **Execution:** a Redis list queue drives the workers (`worker/`), and each run gets its own sandbox container (`sandbox/`).
- **AI agent:** `backend/app/agent/` (tools, prompts, loop) and `backend/app/llm/` (providers).

More detail: [docs/architecture.md](docs/architecture.md) and [docs/HOW_IT_WORKS.md](docs/HOW_IT_WORKS.md).

---

## 🧪 Testing

```bash
# Backend: unit + API + WebSocket tests (in-memory SQLite, no services needed)
(cd backend && pytest --cov=app)

# Worker; DOCKER_TESTS=1 also runs the real sandbox containers
PYTHONPATH=backend DOCKER_TESTS=1 pytest worker/tests --import-mode=importlib

# Frontend: Vitest + React Testing Library with coverage
(cd frontend && npm run coverage)

# Against a running stack (see "Try it" above)
scripts/pilot.sh check
```

CI (`.github/workflows/ci.yml`) runs on every push and PR to `main`:
- **Backend:** ruff, mypy, bandit, then pytest with a coverage floor.
- **Worker:** tests against a real sandbox image.
- **Frontend:** eslint, `tsc`, then Vitest coverage and a production build.

CI never calls Mistral; agent tests use a scripted fake provider.
[docs/vv-report.md](docs/vv-report.md) maps each requirement to its tests.

---

## 🤖 Developing with AI Agents

Concord is designed to be extended using AI agents (like Claude Code):
- **Feature implementation:** "implement a new API endpoint", "add a UI component".
- **Automated debugging:** give it error logs and ask it to "diagnose and fix the crash".
- **Refactoring:** "optimize the member management logic", "improve the execution pipeline".

---

## 📚 Documentation

| Doc | What's in it |
|---|---|
| [PILOT_TEST.md](docs/PILOT_TEST.md) | local pilot: setup, demo accounts, test scenarios |
| [user-guide.md](docs/user-guide.md) | using the app: roles, running code, AI assistant |
| [HOW_TO_RUN.md](docs/HOW_TO_RUN.md), [docker-guide.md](docs/docker-guide.md) | running and operating the stack |
| [architecture.md](docs/architecture.md), [HOW_IT_WORKS.md](docs/HOW_IT_WORKS.md) | design and data flows |
| [api.md](docs/api.md) | REST and WebSocket API |
| [database.md](docs/database.md) | schema |
| [requirements.md](docs/requirements.md), [vv-report.md](docs/vv-report.md) | requirements and verification |
| [development-guide.md](docs/development-guide.md), [runbook.md](docs/runbook.md) | contributing and operations |
