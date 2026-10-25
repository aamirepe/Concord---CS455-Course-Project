# Production deployment

Concord runs on one Linux Docker host. Caddy serves the frontend, proxies the
API and WebSockets, and obtains TLS certificates. PostgreSQL and Redis have
persistent volumes and no published ports. Workers need the host Docker socket
to launch isolated sandbox containers; use a dedicated host for this service.

## Prerequisites

Use Ubuntu 22.04 or newer with Git, curl, OpenSSL, Docker Engine and the Compose
plugin. Allow enough disk for sandbox images and database backups. Point a DNS
name at the host and allow inbound TCP 80 and 443. Restrict SSH to administrators.
The deployment account needs Docker access and write access to `~/concord`.

Clone the repository to `~/concord`. On a fresh Ubuntu host, review and run
`deploy/setup.sh`. It installs Docker if absent, creates local configuration
without replacing existing files, and builds all three sandbox images. If it
adds Docker group membership, log out and back in and run it again. The script
does not start a server with unfinished configuration.

## Configuration and secrets

Keep `.env` and `.env.production` on the server only, with mode 600. Both are
ignored by Git. Start `.env` from `.env.example`; set:

- `APP_ENV=prod`, a fresh `SECRET_KEY`, and a fresh `LLM_ENCRYPTION_KEY`.
  Generate each key with `openssl rand -hex 32` and retain the encryption key
  across deployments so stored provider keys remain readable.
- A strong `POSTGRES_PASSWORD` and matching credentials in `DATABASE_URL`.
  The database hostname is `postgres` and the Redis URL is `redis://redis:6379/0`.
  Hex passwords avoid URL escaping. Do not rotate a volume's database password
  merely by editing the environment; update the PostgreSQL role as well.
- `CONCORD_DOMAIN=concord.example.com` and
  `CORS_ORIGINS=https://concord.example.com`. Use the actual domain.
- An optional `MISTRAL_API_KEY`; leave it empty for project-specific credentials.

Put immutable image references in `.env.production`:

```dotenv
BACKEND_IMAGE=registry.example.com/concord-backend:COMMIT_SHA
WORKER_IMAGE=registry.example.com/concord-worker:COMMIT_SHA
FRONTEND_IMAGE=registry.example.com/concord-frontend:COMMIT_SHA
```

All three images must come from the same tested revision. Build with
`backend/Dockerfile`, `worker/Dockerfile` (repository root context), and
`frontend/Dockerfile.prod` (frontend context). The frontend requires build args
`VITE_API_URL=https://concord.example.com/api` and
`VITE_WS_URL=wss://concord.example.com`; changing runtime environment variables
does not change a built JavaScript bundle. Authenticate Docker to your registry
before pulling private images.

## First deployment and updates

```bash
./deploy/compose.sh config --quiet
./deploy/compose.sh pull
./deploy/compose.sh up -d --remove-orphans --wait --wait-timeout 180
./deploy/compose.sh ps
curl -fsS https://concord.example.com/healthz
```

The backend runs `alembic upgrade head` before serving. Workers start after the
backend is healthy. Verify the web app, sign in, and run a small Python program;
a health check alone does not prove sandbox execution works. Check all three
sandbox images with `docker image ls`.

For updates, take a backup, retain the previous Git SHA and image references,
check out the tested new revision, then repeat the commands above. Do not use
`down -v` on a production database.

## Automated deployment

The `deploy` workflow runs only after `ci` succeeds for a push to `main` in this
repository. It builds and deploys the exact SHA that CI tested. It skips when
required secrets are absent. Configure these GitHub repository secrets:

- `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`: narrowly scoped ECR push access.
- `ECR_REPO_BACKEND`, `ECR_REPO_WORKER`, `ECR_REPO_FRONTEND`: ECR repository names.
- `CONCORD_DOMAIN`: hostname without a URL scheme.
- `DEPLOY_HOST`, `DEPLOY_USER`, `DEPLOY_SSH_KEY`: the server and deployment account.
- `DEPLOY_HOST_FINGERPRINT`: the verified SHA256 SSH host key fingerprint.

The workflow uses AWS region `us-east-1`. The server needs AWS CLI credentials
with ECR pull access, an existing clone at `~/concord`, and a completed `.env`.
The domain in `.env` must match the secret. Deployment preserves the previous
image pins in `.env.production.bak` and waits for service readiness. A failed
rollout fails the workflow; inspect logs and follow the rollback procedure.
No remote infrastructure is created by the workflow.

## Backup and restore

Take backups before migrations and store copies away from the Docker host.
These commands use the configured database identity inside the container:

```bash
mkdir -p backups
chmod 700 backups
./deploy/compose.sh exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > backups/concord.dump
```

Test restoring into a separate database before relying on a backup. To replace
an existing database during an approved maintenance window, stop writers first:

```bash
./deploy/compose.sh stop backend worker
./deploy/compose.sh exec -T postgres sh -c 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists --exit-on-error' < backups/concord.dump
./deploy/compose.sh up -d --wait --wait-timeout 180
```

A database backup does not include live Redis queue or debug state. Stop or
finish outstanding executions before maintenance. Retain secrets securely with
backups, especially the provider encryption key.

## Logs and health

```bash
./deploy/compose.sh logs --tail 100 backend worker caddy
./deploy/compose.sh ps
curl -fsS https://concord.example.com/healthz
```

`/healthz` needs no authentication. It returns `database`, `redis` and
`queue_depth`, with `status=ok` when both dependencies respond. Database failure
returns HTTP 503; Redis failure returns HTTP 200 with `status=degraded` and an
unknown queue depth. Each dependency check has a two-second deadline. Monitor
the JSON status as well as HTTP status. Queue depth counts waiting executions,
not running sandboxes. Logs do not prove workers can launch a sandbox; run a
small execution when diagnosing that path.

## Rollback

Record the deployed SHA and keep the previous image references before updating.
If a deployment fails, stop writes and inspect logs. Check out the previous
revision, restore its image pins to `.env.production`, then pull and start with
`./deploy/compose.sh up -d --wait --wait-timeout 180`. Keep the current `.env`.
Do not roll back code across an incompatible schema change. Review the migration
and either apply its reviewed downgrade or restore the database backup during
maintenance before starting the previous application. Verify health, sign-in,
WebSockets and a sandbox execution after rollback.
