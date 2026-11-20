# WorkflowOps developer commands. On Windows, run from Git Bash/WSL or use the
# equivalent `uv run ...` commands listed in CLAUDE.md.
.PHONY: setup up down migrate seed gen-world lint-data test test-fast lint format typecheck \
        mock api web eval-smoke eval-full demo check

setup:
	uv sync
	cd apps/web && npm ci
	[ -f .env ] || cp .env.example .env

up:
	docker compose up --build -d

down:
	docker compose down

migrate:
	uv run alembic upgrade head

gen-world:
	uv run python scripts/generate_world.py

seed: gen-world migrate

mock:
	uv run uvicorn mock_business_systems.app:app --port 8100 --reload

api:
	uv run uvicorn backend.api.main:app --port 8000 --reload

web:
	cd apps/web && npm run dev

test:
	uv run pytest -q

test-fast:
	uv run pytest -q -m "not integration"

lint:
	uv run ruff check .
	uv run ruff format --check .

format:
	uv run ruff format .
	uv run ruff check --fix .

typecheck:
	uv run mypy

check: lint typecheck test

# Phase 7 targets (not implemented yet; they fail loudly rather than pretend).
lint-data gen-eval eval-smoke eval-full:
	@echo "$@: not implemented yet (Phase 7)"; exit 1

demo:
	@echo "Start 'make mock', 'make api', 'make web', then follow docs/user-guides/demo.md"
