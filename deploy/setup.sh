#!/usr/bin/env bash
# Prepare an Ubuntu server. Review docs/deployment.md before running.
set -euo pipefail
if ! command -v docker >/dev/null; then
  curl -fsSL https://get.docker.com | sudo sh
  sudo usermod -aG docker "$USER"
  echo "Log out and in to activate Docker access, then run this script again."
  exit 0
fi
docker info >/dev/null
docker compose version >/dev/null
if [ ! -d "$HOME/concord/.git" ]; then
  git clone https://github.com/aamirepe/Concord---CS455-Course-Project.git "$HOME/concord"
fi
cd "$HOME/concord"
umask 077
if [ ! -f .env ]; then
  cp .env.example .env
  secret=$(openssl rand -hex 32)
  database_password=$(openssl rand -hex 24)
  encryption_key=$(openssl rand -hex 32)
  sed -i "s/^SECRET_KEY=.*/SECRET_KEY=$secret/; s/^POSTGRES_PASSWORD=.*/POSTGRES_PASSWORD=$database_password/; s|^DATABASE_URL=.*|DATABASE_URL=postgresql+asyncpg://concord:$database_password@postgres:5432/concord|; s/^LLM_ENCRYPTION_KEY=.*/LLM_ENCRYPTION_KEY=$encryption_key/; s/^APP_ENV=.*/APP_ENV=prod/" .env
fi
if [ ! -f .env.production ]; then
  cat > .env.production <<'CONFIG'
BACKEND_IMAGE=
WORKER_IMAGE=
FRONTEND_IMAGE=
CONFIG
fi
chmod 600 .env .env.production
for language in python cpp nodejs; do
  dockerfile=sandbox/Dockerfile
  image=concord-sandbox:latest
  if [ "$language" != python ]; then
    dockerfile="sandbox/Dockerfile.$language"
    image="concord-sandbox-$language:latest"
  fi
  docker build -t "$image" -f "$dockerfile" sandbox
done
echo "Set image references, domain and CORS_ORIGINS using docs/deployment.md before starting."
