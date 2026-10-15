# Concord — Runbook

## Local development

```bash
cp .env.example .env
docker compose up -d
# Frontend:  http://localhost:5173
# Backend:   http://localhost:8000/docs
```

The first run builds the sandbox image and seeds an empty Postgres. After
that, start/stop is `docker compose up -d` / `docker compose down`.

## Rebuilding a single service

```bash
docker compose build backend
docker compose up -d --no-deps backend
```

## Database migrations

```bash
# Create a new migration
docker compose exec backend alembic revision --autogenerate -m "describe change"

# Apply
docker compose exec backend alembic upgrade head

# Roll back one
docker compose exec backend alembic downgrade -1
```

## Health checks

```bash
curl -fsS http://localhost:8000/healthz
curl -fsS http://localhost:8000/metrics/executions/summary
```

If `/healthz` returns OK but executions are stuck, check:
1. Are workers connected to Redis? `docker compose ps worker` should show
   them healthy. Tail logs: `docker compose logs -f worker`.
2. Is the sandbox image built? `docker images | grep concord-sandbox`.
3. Did the per-tenant docker socket go away?
   `docker exec backend docker ps` — if that fails, the host's dockerd is down.

## Common failure modes

| Symptom | Likely cause | Fix |
|---|---|---|
| 401 on every request | JWT secret rotated but clients still hold old tokens | Users log in again |
| WebSocket drops every 30s | A reverse proxy is closing idle connections | Set proxy timeouts ≥ 1h |
| Execution stays `queued` forever | Worker can't reach Redis or DB | `docker compose logs worker` |
| Execution `starting` for a long time | Debugpy waiting for client that never connects | Non-debug run, not debug — should be fast |
| Sandbox `Read-only file system` | Code tried to write — by design | Adjust the script |

## Backing up Postgres

```bash
docker compose exec postgres pg_dump -U concord concord | gzip > backup-$(date +%F).sql.gz
```

Restoring:

```bash
gunzip -c backup-2026-02-15.sql.gz | docker compose exec -T postgres psql -U concord -d concord
```
