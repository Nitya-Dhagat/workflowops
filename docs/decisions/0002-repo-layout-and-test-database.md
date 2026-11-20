# 0002 — Repository layout and test database

**Status:** accepted · 2026-11-20

## Context
The brief suggests `apps/{api,web}` plus a `backend/` package. Python packages under `apps/`
complicate imports. Docker was not running on the development machine, so Postgres was not
available locally when Phase 0/1 were built.

## Decision
1. One importable Python package, `backend/`, holds all platform code; the API lives in
   `backend/api/` (entrypoint `backend.api.main:app`). `apps/web` holds the Next.js app.
   `apps/api` is not created; `infra/docker/python.Dockerfile` builds the API image.
2. Models use portable SQLAlchemy types (`Uuid`, `JSON` with a `JSONB` variant on Postgres,
   string-valued enums), so the same code runs on PostgreSQL (system of record) and SQLite.
3. Local tests default to a per-test SQLite file. Setting `WFO_TEST_DATABASE_URL` runs the
   entire suite on Postgres; CI does this in the `postgres-migrations` job, which also runs
   `alembic upgrade head`, `alembic check` and `downgrade base` on Postgres.
4. The mock business systems run in-process in tests via `httpx.ASGITransport`; in compose
   they are a separate container so network faults are real.

## Consequences
- Postgres-specific behavior (e.g. `SELECT ... FOR UPDATE` on approvals) is only exercised in
  CI until Docker is available locally. **As of this ADR the Postgres path has not been run
  on this machine.**
- SQLite returns naive datetimes; code normalizes with `_aware()` before comparing.
