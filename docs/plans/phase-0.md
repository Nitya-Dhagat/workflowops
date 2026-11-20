# Phase 0 — Scaffolding

**Status:** done (2026-11-20), with one gap noted below.

## Plan
- Repo skeleton per brief §12 (adapted, see ADR-0002), `CLAUDE.md`, `pyproject.toml` (uv,
  pinned), ruff, mypy --strict, pytest, pre-commit, GitHub Actions CI.
- `docker-compose.yml`: api, web, postgres, mock-business-systems, otel-collector, prometheus,
  grafana (observability services are a skeleton until Phase 6).
- ADR-0001 (stack and versions). Health endpoint. DB + Alembic migrations connected.

## Acceptance checks
- `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy` clean — **met**.
- `GET /health` returns ok — **met** (tested live).
- `alembic upgrade head` → `alembic check` (no drift) → `downgrade base` — **met on SQLite**
  (`tests/integration/test_migrations.py`); Postgres runs in CI only.
- `docker compose up` — **not verified**: the Docker daemon was not running on the dev machine.
  Dockerfiles and compose are written but untested. First thing to check once Docker is up.
