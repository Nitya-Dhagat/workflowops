# Phase 1 — Non-AI foundation

**Status:** done (2026-11-20).

## Plan (files → purpose)
- `backend/domain/` — status enums, Pydantic contracts, `state_machine.py` (legal transitions).
- `backend/database/models.py` — all brief §6 entities + `outbox_events`, `idempotency_keys`;
  migration `0001`.
- `scripts/generate_world.py` → `data/seed/world.json`, `data/seed/traps.yaml`,
  `data/policies/*.md` (50 partners, 11 users, 201 tickets, 30 policies, 9 traps; seed 1234).
- `mock_business_systems/` — CRM, ticketing, KB (BM25), notifications; fault injection
  (`timeout`, `error_500`, `error_400`, `malformed`, `slow`, `ack_lost`) via `X-Fault` header or
  `POST /_control/faults`; server-side `Idempotency-Key` (toggleable); `/_control/state` for
  final-state scoring.
- `backend/integrations/` — connector interface + error classification (retryable/ambiguous).
- `backend/frameworks/base.py` — adapter contract; `frameworks/rules/` — LLM-free agents.
- Pulled forward from Phase 2 because the end-to-end flow needs them (minimal versions):
  `tools/gateway.py` (allowlists, arg validation, redacted audit log), `policy/` (catalog,
  facts, deterministic validator), `approvals/service.py`, `orchestration/executor.py`.
- `backend/orchestration/engine.py` — durable state-machine engine (Mode A).
- API: requests, workflows (run/list/detail/events/cancel/resume), approvals, integrations.
- Web: overview, requests (submit + list + search), request detail / run view, approvals.

## Acceptance checks — all met
- Rule-based workflow completes a request end to end without any LLM (happy path, demo
  scenario with missing SOC 2, partial approval, rejections, duplicates, escalations).
- World generation byte-reproducible (test compares a fresh generation to committed files).
- Failure handling: transient 5xx retried; permanent 4xx not retried; retries bounded; lost-ack
  writes recovered by read-back with server idempotency disabled; crash between actions and
  crash after side effect both recover with no duplicate ticket.
- Approval guards: hash mismatch 409, non-reviewer 403, second decision 409, expiry handled.
- 62 tests passing; ruff, mypy --strict, `next lint`, `next build` clean.
- Manually exercised the live stack (mock + API + web dev server) through the browser:
  submit → awaiting approval → approve access / reject notification → completed + verified.

## Deferred
- SSE event stream (Phase 6; a polling JSON endpoint exists).
- Replay endpoint and comparison view (needs a second framework; Phase 5).
- `request_changes` → re-planning loop, approval escalation, JWT auth (Phase 2).
- Docker/Postgres verification on the dev machine (Docker daemon not running).
