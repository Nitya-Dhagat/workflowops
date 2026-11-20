# WorkflowOps — guide for Claude Code

Multi-framework, human-supervised AI operations platform. The same business workflow
(`partner_onboarding`) runs through interchangeable agent frameworks (rules → LLM baseline →
Google ADK → LangGraph) behind one typed contract, while the **platform** owns business rules,
safety, approvals, execution, verification, and measurement. Full brief: `docs/PROJECT_BRIEF.md`
(if present) and `docs/plans/`.

## Architecture (dependency direction is one-way, top → bottom)

```
apps/web (Next.js)  ──REST──▶  backend/api (FastAPI, no framework logic)
backend/orchestration   workflow engine: durable state machine, executor, bindings
backend/frameworks/*    adapters implementing frameworks/base.py (rules today; adk/langgraph later)
backend/policy          deterministic policy engine + action catalog (authoritative, no LLM)
backend/approvals       hash-bound, immutable human decisions
backend/tools/gateway   ONLY path from agents/executor to external systems
backend/integrations    connectors (CRM, ticketing, KB, notifications) over HTTP
mock_business_systems   separate FastAPI service with fault injection (/_control/*)
backend/database        SQLAlchemy models, Alembic migrations, reference-data seed
```

## Directory map

- `backend/domain/` enums, Pydantic contracts, `state_machine.py` (single source of legal transitions)
- `backend/frameworks/rules/` LLM-free reference implementation of all agent tasks
- `scripts/generate_world.py` deterministic world generator → `data/seed/`, `data/policies/`
- `tests/{unit,integration}` — integration tests run API + mock systems in-process (ASGI)
- `docs/decisions/` ADRs · `docs/plans/phase-N.md` per-phase plans

## Commands (Windows: run the `uv`/`npm` forms; `make` works in Git Bash/WSL/CI)

| Task | Command |
|---|---|
| Setup | `uv sync` · `cd apps/web && npm ci` · copy `.env.example` → `.env` |
| Mock systems | `uv run uvicorn mock_business_systems.app:app --port 8100` |
| API | `uv run uvicorn backend.api.main:app --port 8000` (SQLite by default) |
| Web | `npm --prefix apps/web run dev` (http://localhost:3000) |
| Full stack | `docker compose up --build` (Postgres + observability skeleton) |
| Tests | `uv run pytest -q` · fast: `uv run pytest -q -m "not integration"` |
| Tests on Postgres | set `WFO_TEST_DATABASE_URL=postgresql+asyncpg://...` then `uv run pytest` |
| Lint / format | `uv run ruff check .` · `uv run ruff format .` |
| Typecheck | `uv run mypy` (strict) |
| Migrate | `uv run alembic upgrade head` (URL from `WFO_DATABASE_URL`; local MySQL: `mysql+aiomysql://...`, ADR-0005) |
| New migration | `uv run alembic revision --autogenerate -m "..."` then review it |
| Regenerate world | `uv run python scripts/generate_world.py` (must be byte-identical) |
| Web checks | `npm --prefix apps/web run lint` · `npm --prefix apps/web run build` |
| Eval | not implemented yet (Phase 7) |

## Conventions

- Python 3.12, async everywhere, Pydantic v2 models at every boundary, `mypy --strict` clean.
- Statuses are explicit `StrEnum`s; every status change goes through `_transition` (writes an
  `agent_events` row + `outbox_events` row).
- Each stage commits its output together with its transition → `advance(run_id)` is resumable.
- Side effects only via `ToolGateway.execute_action` with an approved, hash-matching action and
  an idempotency key; ambiguous failures are read back (`bindings.locate`) before retrying.
- Prompts (Phase 3+) live in `backend/agents/<name>/prompts/vN.md`; record the version.
- Next.js 16 in `apps/web` differs from older versions — read `node_modules/next/dist/docs/`
  first (e.g. `useParams` needs a `<Suspense>` boundary with `cacheComponents`).
- Conventional commits (`feat:`, `fix:`, `test:`, `docs:`, `chore:`), small and per logical step.

## Working agreements

1. Plan each phase in `docs/plans/phase-N.md` before coding; execute without waiting unless blocked.
2. Keep the system runnable end to end after every phase; tests must pass.
3. No feature is done without tests; run them and fix failures before moving on.
4. Record non-obvious decisions as ADRs in `docs/decisions/NNNN-title.md`.
5. Verify ADK / LangGraph / CrewAI / OpenTelemetry / Next.js APIs against current docs; pin versions.
6. Never fabricate results; every number comes from a stored run. Report negative findings.
7. Complete, tested core before breadth; no stretch features before the Definition of Done.
8. End each phase with: what works, what was tested, what is deferred, how to run it.

## Safety invariants (enforced in code, covered by tests)

1. No action executes unless it passed schema, required-field, permission, business-rule, risk,
   approval, and idempotency checks in the deterministic policy engine.
2. Risk comes from `policy/catalog.py`, never from the planner. High risk always needs a human;
   medium is configurable (`WFO_MEDIUM_RISK_REQUIRES_APPROVAL`); low is read-only.
3. Agents can only call tools on their allowlist (`tools/gateway.py`); denials are logged.
4. Retries are bounded; one idempotency key never produces two side effects.
5. Tool inputs/outputs are logged redacted (`core/logging.redact`).
6. Retrieved or user-supplied text is untrusted data; the policy engine never reads it as
   instructions (see `test_prompt_injection_is_ignored`).
7. Approvals are immutable, attributed, and bound to the action hash; a changed action needs
   re-approval.
8. Runs resume after a crash without duplicate side effects (`test_failures_and_recovery.py`).

## Never do

- Never bypass the policy engine or call connectors directly from an agent/adapter.
- Never let agents or adapters hold DB sessions or connector handles.
- Never let framework objects escape `backend/frameworks/<name>/`.
- Never log secrets or unredacted PII; never commit `.env` or keys.
- Never invent metrics, benchmark numbers, or screenshots.
- Never tune prompts on the test split.
