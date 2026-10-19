#!/bin/sh
# Load operator configuration and pinned image references without executing them.
set -eu
cd "$(dirname "$0")/.."
for config in .env .env.production; do
  if [ ! -f "$config" ]; then
    echo "Missing $config; follow docs/deployment.md" >&2
    exit 1
  fi
done
exec docker compose --env-file .env --env-file .env.production -f docker-compose.prod.yml "$@"
