#!/usr/bin/env bash
# Local pilot of Concord: one command to build, start, seed and check the full
# stack in Docker. See docs/PILOT_TEST.md for the tester's guide.
#
#   scripts/pilot.sh up       build + start everything, seed demo data, run the checks
#   scripts/pilot.sh check    re-run the automated checks (saved under pilot-results/)
#   scripts/pilot.sh seed     re-create demo users/project (never overwrites typed code)
#   scripts/pilot.sh status   what's running, and the URLs
#   scripts/pilot.sh logs [service]   follow logs (backend, worker, frontend, ...)
#   scripts/pilot.sh collect  save logs + versions into pilot-results/ to share
#   scripts/pilot.sh stop     stop containers, keep data
#   scripts/pilot.sh reset    delete ALL pilot data (database, redis) and stop
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
RESULTS="$ROOT/pilot-results"
APP_SERVICES=(postgres redis backend worker frontend)

bold() { printf '\033[1m%s\033[0m\n' "$*"; }
info() { printf '  %s\n' "$*"; }
fail() { printf '\033[31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

# --- docker access -----------------------------------------------------------
# Right after `usermod -aG docker $USER`, the current login session doesn't have
# the group yet; `sg docker` runs a command with it without logging out.
DOCKER_MODE=""
docker_mode() {
  [[ -n "$DOCKER_MODE" ]] && return
  command -v docker >/dev/null || fail "Docker is not installed. See docs/PILOT_TEST.md, section 1."
  if docker info >/dev/null 2>&1; then
    DOCKER_MODE=direct
  elif getent group docker | grep -qw "$USER"; then
    # In the docker group, but this login session predates it.
    if ! command -v sg >/dev/null; then
      fail "You were added to the docker group after this terminal started. Open a new login shell (on WSL: run 'wsl --shutdown' in Windows, then reopen Ubuntu) and retry."
    fi
    sg docker -c "docker info" >/dev/null 2>&1 || fail "Docker daemon not reachable. Try: sudo systemctl start docker"
    DOCKER_MODE=sg
  else
    fail "No permission to use Docker. Run: sudo usermod -aG docker \$USER   (then re-run this script)"
  fi
}

dc() {
  docker_mode
  if [[ "$DOCKER_MODE" == direct ]]; then
    docker compose "$@"
  else
    sg docker -c "docker compose $(printf '%q ' "$@")"
  fi
}

# --- setup -------------------------------------------------------------------
rand_hex() { head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n'; }

# In-place edit that works with both GNU sed (Linux/WSL) and BSD sed (macOS).
sed_inplace() {  # [sed options] expression file
  local file="${!#}"
  sed -i.bak "${@:1:$#-1}" "$file" && rm -f "$file.bak"
}

port_busy() {
  if command -v ss >/dev/null; then
    ss -ltnH "sport = :$1" 2>/dev/null | grep -q .
  else
    lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1
  fi
}

ensure_env() {
  if [[ ! -f .env ]]; then
    cp .env.example .env
    chmod 600 .env
    info "created .env from .env.example"
  fi
  # Replace the example placeholders once; never touch values someone set.
  # (Changing LLM_ENCRYPTION_KEY later would make saved project AI keys unreadable.)
  if grep -q '^SECRET_KEY=change-me-in-prod-32-bytes-minimum$' .env; then
    sed_inplace "s/^SECRET_KEY=.*/SECRET_KEY=$(rand_hex)/" .env
    info "generated SECRET_KEY"
  fi
  if grep -q '^LLM_ENCRYPTION_KEY=change-me$' .env; then
    sed_inplace "s/^LLM_ENCRYPTION_KEY=.*/LLM_ENCRYPTION_KEY=$(rand_hex)/" .env
    info "generated LLM_ENCRYPTION_KEY"
  fi
  if grep -q '^MISTRAL_API_KEY=$' .env; then
    info "no MISTRAL_API_KEY in .env: the AI panel will ask the project owner for a key"
  else
    info "AI assistant: server key found in .env"
  fi
}

check_ports() {
  local busy=()
  for port in 5432 6379 8000 5173; do
    if port_busy "$port"; then busy+=("$port"); fi
  done
  if ((${#busy[@]})); then
    # Fine if they're ours from an earlier run.
    if [[ -n "$(dc ps -q backend 2>/dev/null)" ]]; then return; fi
    fail "port(s) ${busy[*]} already in use by something else. Stop it (e.g. a local postgres: sudo systemctl stop postgresql) and retry."
  fi
}

wait_for() {  # name url seconds
  local name=$1 url=$2 limit=$3 waited=0
  printf '  waiting for %s' "$name"
  until curl -fs -o /dev/null "$url"; do
    sleep 2; waited=$((waited + 2)); printf '.'
    if ((waited >= limit)); then
      echo
      fail "$name did not come up within ${limit}s. Look at: scripts/pilot.sh logs $4"
    fi
  done
  echo " ok"
}

pilot_py() { dc exec -T backend python /scripts/pilot.py "$@"; }

summary() {
  local pid
  pid=$(grep -o 'PROJECT_ID=[0-9]*' "$RESULTS/seed.txt" 2>/dev/null | cut -d= -f2 || true)
  echo
  bold "Concord is running"
  info "App:        http://localhost:5173"
  info "API docs:   http://localhost:8000/docs"
  info "Accounts:   alice (owner), bob (editor), carol (viewer), dave (debugger), erin (not a member)"
  info "Password:   pilot-pass-123 (all accounts)"
  [[ -n "$pid" ]] && info "Project:    \"Pilot demo\" (ID $pid)"
  info "Guide:      docs/PILOT_TEST.md    Feedback form: pilot-results/feedback.md"
  info "Tip:        use a normal and a private browser window to be two people at once"
}

# --- commands ----------------------------------------------------------------
cmd_up() {
  bold "1/5 Preflight"
  docker_mode
  info "docker: $(docker_version) (access: $DOCKER_MODE)"
  ensure_env
  check_ports
  mkdir -p "$RESULTS"
  [[ -f "$RESULTS/feedback.md" ]] || cp docs/pilot-feedback-template.md "$RESULTS/feedback.md"

  bold "2/5 Building images (first time: 5-10 minutes)"
  dc build

  bold "3/5 Starting services"
  dc up -d "${APP_SERVICES[@]}"
  wait_for "backend" "http://localhost:8000/healthz" 240 backend
  wait_for "frontend" "http://localhost:5173" 240 frontend

  bold "4/5 Seeding demo data"
  pilot_py seed | tee "$RESULTS/seed.txt"

  bold "5/5 Automated checks"
  cmd_check || true
  summary
}

cmd_check() {
  mkdir -p "$RESULTS"
  local out
  out="$RESULTS/check-$(date +%Y%m%d-%H%M%S).txt"
  local rc=0
  pilot_py check "$@" | tee "$out" || rc=$?
  info "saved to ${out#"$ROOT"/}"
  return $rc
}

docker_version() {
  if [[ "$DOCKER_MODE" == direct ]]; then docker --version; else sg docker -c "docker --version"; fi
}

cmd_collect() {
  mkdir -p "$RESULTS"
  local dir
  dir="$RESULTS/logs-$(date +%Y%m%d-%H%M%S)"
  mkdir -p "$dir"
  for s in "${APP_SERVICES[@]}"; do dc logs --no-color --timestamps "$s" > "$dir/$s.log" 2>&1 || true; done
  {
    echo "git: $(git rev-parse --abbrev-ref HEAD) $(git rev-parse --short HEAD) ($(git status --porcelain | wc -l) uncommitted changes)"
    docker_version
    dc version
    dc ps
  } > "$dir/versions.txt" 2>&1
  # Logs never contain the .env file, but double-check no API key slipped in.
  if grep -rlE 'mstrl_|sk-[A-Za-z0-9]{20,}' "$dir" >/dev/null 2>&1; then
    for f in "$dir"/*; do sed_inplace -E 's/(mstrl_|sk-)[A-Za-z0-9_-]+/\1[REDACTED]/g' "$f"; done
  fi
  info "logs saved to ${dir#"$ROOT"/} (share this folder with the feedback file)"
}

case "${1:-help}" in
  up) cmd_up ;;
  check) shift; cmd_check "$@" ;;
  seed) mkdir -p "$RESULTS"; pilot_py seed | tee "$RESULTS/seed.txt" ;;
  status) dc ps; summary ;;
  logs) shift; dc logs -f --tail=200 "$@" ;;
  collect) cmd_collect ;;
  stop) dc stop ;;
  reset)
    read -r -p "Delete ALL pilot data (accounts, projects, code)? Type 'reset' to confirm: " answer
    [[ "$answer" == reset ]] || fail "cancelled"
    dc down -v --remove-orphans
    rm -f "$RESULTS/seed.txt"
    info "all pilot data deleted; run 'scripts/pilot.sh up' to start fresh"
    ;;
  *) sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//' ;;
esac
