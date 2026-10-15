.PHONY: help dev up down logs test lint typecheck build sandbox docs pilot pilot-check pilot-stop pilot-reset

help:
	@echo "Targets:"
	@echo "  dev        Bring up the full local stack"
	@echo "  down       Stop and remove containers"
	@echo "  logs       Tail logs from all services"
	@echo "  test       Run backend unit + integration tests"
	@echo "  lint       Run ruff, mypy, bandit"
	@echo "  sandbox    Build the sandbox image"
	@echo "  build      Build frontend production assets"
	@echo "  pilot      Local pilot: build, start, seed demo data, run checks (docs/PILOT_TEST.md)"
	@echo "  pilot-check  Re-run the pilot's automated checks"
	@echo "  pilot-stop / pilot-reset  Stop the pilot / delete all pilot data"

dev:
	docker compose up -d
	@echo "Frontend: http://localhost:5173"
	@echo "Backend:  http://localhost:8000/docs"

down:
	docker compose down

logs:
	docker compose logs -f

test:
	docker compose exec backend pytest

lint:
	docker compose exec backend ruff check .
	docker compose exec backend mypy app
	docker compose exec backend bandit -r app -ll

sandbox:
	docker build -t concord-sandbox:latest ./sandbox

build:
	docker compose exec frontend npm run build

pilot:
	scripts/pilot.sh up

pilot-check:
	scripts/pilot.sh check

pilot-stop:
	scripts/pilot.sh stop

pilot-reset:
	scripts/pilot.sh reset
