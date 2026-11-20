# 0005 — Local MySQL as the development database; Gemini as the single LLM provider

**Status:** accepted · 2026-11-20 (amends ADR-0002 and the brief's Postgres default)

## Context
The owner runs a local MySQL server and does not want to depend on Docker. The LLM provider for
all frameworks will be Google Gemini (key in `.env` as `GOOGLE_API_KEY`).

## Decision
- Supported databases: **MySQL** (local dev, driver `aiomysql==0.3.2`), PostgreSQL (docker-compose
  / CI), SQLite (offline test suite). Models already use portable types: `Uuid` (CHAR(32) on
  MySQL), `JSON` (JSONB variant only on Postgres), sized `String`s, naive datetimes normalized
  with `_aware()`.
- URL from `.env`: `WFO_DATABASE_URL=mysql+aiomysql://USER:PASSWORD@localhost:3306/workflowops`.
  On MySQL/Postgres the schema comes from `uv run alembic upgrade head` (the app only
  auto-creates tables on SQLite).
- Docker is optional; docker-compose stays for the Postgres + observability setup.
- Frameworks (baseline, Google ADK, LangGraph; CrewAI optional) all use Gemini through the shared
  `LLMClient` with one model/config, so comparisons stay fair. No Anthropic key is planned.

## Consequences
- The MySQL path is **not yet verified**: no MySQL URL was configured when this ADR was written.
- Test suite stays on SQLite; `WFO_TEST_DATABASE_URL` can point it at MySQL for a manual check.
