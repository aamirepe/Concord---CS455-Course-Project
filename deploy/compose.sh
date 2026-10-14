#!/bin/sh
# docker compose for the production stack, with the image names that the last
# deploy wrote to .env.deploy. Run from the repo root on the server, e.g.
#   ./deploy/compose.sh ps
#   ./deploy/compose.sh logs -f backend
set -eu
cd "$(dirname "$0")/.."
if [ ! -f .env.deploy ]; then
  echo "no .env.deploy here; the deploy workflow writes it" >&2
  exit 1
fi
set -a
# shellcheck source=/dev/null  # written at deploy time
. ./.env.deploy
set +a
exec docker compose -f docker-compose.prod.yml "$@"
