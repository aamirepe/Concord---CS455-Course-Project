#!/usr/bin/env bash
# Provision a fresh Ubuntu 22.04 EC2 / Oracle Cloud VM for Concord.
# Idempotent — safe to re-run.

set -euo pipefail

echo "==> Installing Docker"
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker "$USER"

echo "==> Installing Docker Compose plugin"
sudo apt-get update
sudo apt-get install -y docker-compose-plugin

echo "==> Cloning the repo"
if [ ! -d "$HOME/concord" ]; then
  git clone https://github.com/YOUR-ORG/concord.git "$HOME/concord"
fi
cd "$HOME/concord"

echo "==> Creating .env from .env.example"
if [ ! -f .env ]; then
  cp .env.example .env
  # Generate a real secret.
  sed -i "s/change-me-in-prod-32-bytes-minimum/$(openssl rand -hex 32)/" .env
fi

echo "==> Building sandbox image"
docker build -t concord-sandbox:latest ./sandbox

echo "==> Starting the stack"
docker compose -f docker-compose.prod.yml up -d

echo "==> Done. Visit http://<host>"
echo "    First-time TLS: point your domain's A record at this VM and Caddy will"
echo "    auto-issue a Let's Encrypt cert on the next request."
