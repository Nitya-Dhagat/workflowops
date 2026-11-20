# 0001 — Tech stack and pinned versions

**Status:** accepted · 2026-11-20

## Context
The brief fixes the default stack (Section 2). Versions must be pinned and recorded, and the
framework APIs verified against current docs because training-time knowledge may be stale.

## Decision
Use the default stack. Versions resolved by `uv` / `npm` on 2026-11-20 and pinned exactly
(`pyproject.toml` uses `==`; `uv.lock` and `package-lock.json` are committed):

| Package | Version | Package | Version |
|---|---|---|---|
| Python | 3.12 | fastapi | 0.165.0 |
| pydantic | 2.14.4 | pydantic-settings | 2.16.3 |
| sqlalchemy | 2.1.9 | alembic | 1.20.1 |
| asyncpg | 0.32.0 | aiosqlite | 0.22.1 |
| httpx | 0.28.1 | structlog | 26.1.0 |
| uvicorn | 0.54.0 | pytest | 9.2.0 |
| pytest-asyncio | 1.5.0 | hypothesis | 6.170.17 |
| ruff | 0.17.6 | mypy | 2.4.1 |
| faker | 40.63.0 | uv | 0.8.4 |
| next | 16.4.6 | react / react-dom | 19.3.0 |
| tailwindcss | 4.3.3 | @tanstack/react-query | 5.108.2 |

Google ADK, LangGraph, LiteLLM/provider SDKs and OpenTelemetry are **not yet installed**; they
are added (and verified against their current docs) in Phases 3–6 with their own ADR entries.

## Consequences
- Next.js 16 has breaking changes vs. older versions (e.g. `cacheComponents` makes
  `useParams` suspend, so dynamic pages need a `<Suspense>` boundary). Its bundled docs in
  `apps/web/node_modules/next/dist/docs/` are the reference.
- shadcn/ui and Recharts are deferred to Phase 8; Phase 1 UI uses plain Tailwind components.
- The Faker version is part of the world-generation contract: bumping it may change
  `data/seed/world.json`, which the reproducibility test will catch.
