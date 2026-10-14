# Concord — Developer Setup Guide

## Prerequisites
- Docker 24+ with Compose plugin
- Python 3.12 (for running tests without Docker)
- Node 20 (for frontend dev without Docker)
- GNU make (optional, the Makefile is convenience)

## First-time setup

```bash
git clone https://github.com/aamirepe/Concord---CS455-Course-Project.git concord
cd concord
cp .env.example .env
docker compose up -d
docker compose logs -f backend    # wait for "Application startup complete"
```

Visit:
- Frontend: http://localhost:5173
- API docs: http://localhost:8000/docs
- Health: http://localhost:8000/healthz

Sign up, create a project, open a session, and start editing.

## Running tests

```bash
# All backend unit + integration
docker compose exec backend pytest

# Just the CRDT convergence test
docker compose exec backend pytest tests/test_concurrent_edits.py

# Sandbox security (requires DOCKER_TESTS=1)
DOCKER_TESTS=1 docker compose exec backend pytest worker/tests/test_sandbox_security.py
```

## Local dev without Docker (optional)

If you have Postgres + Redis installed natively:

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt   # app + test/lint tools
export DATABASE_URL=postgresql+asyncpg://concord:concord@localhost:5432/concord
export REDIS_URL=redis://localhost:6379/0
export SECRET_KEY=$(openssl rand -hex 32)
alembic upgrade head
uvicorn app.main:app --reload

# in another terminal
cd worker
PYTHONPATH=../backend python -m worker.app.worker
```

Frontend:

```bash
cd frontend
npm install
npm run dev
```

## Code style

- Backend: `ruff format` + `ruff check` + `mypy --strict`. The CI pipeline fails on lint errors.
- Frontend: ESLint + `tsc -b --noEmit`.

## Adding a new REST endpoint

1. Add the Pydantic schemas in `app/schemas/__init__.py`.
2. Add the route in the appropriate `app/api/<area>.py` router.
3. Add a permission check via `Depends(require_role(...))` if it's a session-scoped endpoint.
4. Add a test in `tests/test_*.py`.
5. Document in `docs/api.md`.

## Adding a new WebSocket message type

1. Add a `CollabMessage` union member in `frontend/src/lib/ws.ts`.
2. Add the handler in `backend/app/realtime/gateway.py`.
3. Add the broadcast site if it's an event type.
4. Add a concurrency test if it affects CRDT state.
