# WorkflowOps: Master Build Prompt (project brief)

> Source of truth for scope. Copied from the owner's original brief on 2026-11-20.
> Phase status lives in `docs/plans/`; decisions in `docs/decisions/`.

## 0. Role and working agreements

Lead engineer building **WorkflowOps**, a multi-framework, human-supervised AI operations
platform; flagship portfolio project for SDE / ML / AI-engineering roles. It must be **real,
tested, runnable, and honestly measured**.

1. Plan before code (`docs/plans/phase-N.md`); don't wait for approval unless blocked or the
   decision is expensive to reverse.
2. Vertical slices; after every phase `docker compose up` plus the test suite must pass.
3. Tests with every feature; run them and fix failures before moving on.
4. Small conventional commits.
5. Record decisions as ADRs (`docs/decisions/NNNN-title.md`).
6. Verify Google ADK, LangGraph, CrewAI, OpenTelemetry, Next.js APIs against current docs; pin
   exact versions and note them in an ADR.
7. Never fabricate results; numbers come from stored runs; report negative findings.
8. Keep it honest and minimal; no stretch features before the Definition of Done.
9. Never commit secrets; `.env.example` only.
10. End each phase with: what works, what was tested, what is deferred, exact run command.

## 1. Product vision

One operational automation platform that runs the **same business workflow** through
**different agent frameworks**, connects to simulated business systems, enforces deterministic
safety and approval rules, and **measures** whether the automation works.

Core principle: agents are replaceable components. Business rules, integrations, execution
safety, evaluation and observability stay under platform control. **The LLM is never the final
authority on whether a sensitive action is allowed.**

Must demonstrate: orchestration behind a framework-agnostic contract; deterministic safety
around non-deterministic components; HITL approvals with durable pause/resume; idempotent,
verified execution; full observability (traces, cost, latency); rigorous reproducible
evaluation against a non-agentic baseline.

Non-goals (v1): general autonomous enterprise agent; real production accounts; unrestricted
tools; custom model training; heavy RBAC; visual workflow builder; every framework.

## 2. Tech stack (deviate only with an ADR)

Backend: Python 3.12+, FastAPI, Pydantic v2, SQLAlchemy 2 async + Alembic, `uv`.
Database: PostgreSQL (pgvector only if retrieval needs it). Frameworks: Google ADK and
LangGraph required; CrewAI optional/late. LLM: thin `LLMClient` over a provider SDK or LiteLLM
so both frameworks use the same model/config. Frontend: Next.js App Router, React, TS,
Tailwind, shadcn/ui, TanStack Query, Recharts. Realtime: SSE. Observability: OpenTelemetry,
Prometheus, Grafana, structlog JSON. Testing: pytest, pytest-asyncio, hypothesis, Playwright.
Quality: ruff, mypy --strict on backend, pre-commit, GitHub Actions. Infra: Docker,
docker-compose (api, web, postgres, otel-collector, prometheus, grafana, mock-business-systems).

**Deterministic fake LLM**: `FakeLLMClient` (scripted/recorded responses keyed by test case) so
unit/integration/CI run offline, free, deterministic. Live models only for benchmarks/demos;
record live runs as replayable cassettes.

## 3. CLAUDE.md

Root `CLAUDE.md` (<~150 lines): summary, layer map, directory map, exact commands, conventions,
working agreements, safety invariants, "never do" list. Keep updated.

## 4. Architecture

Frontend → API (no framework logic) → platform workflow engine (durable state machine,
scheduling, parallelism, retries, pause/resume) → framework adapters (ADK | LangGraph | CrewAI)
and the deterministic policy & safety engine → Tool gateway (only path to anything real;
validates, authorizes, logs, redacts) → connectors (CRM, ticketing, KB, notifications) →
PostgreSQL + OTel + Prometheus/Grafana.

Rules: framework objects never leave `backend/frameworks/<name>/`; agents never touch DB or
connectors, only gateway tools (gateway injects run context, enforces permissions, writes
`tool_invocations`); canonical state, approvals and execution are platform-owned in Postgres.

Two orchestration depths: **Mode A "stage executor"** (platform runs the stage graph, each
stage calls `adapter.execute(task)`; required first) and **Mode B "native orchestration"**
(framework runs investigation→plan→validate with its own primitives; platform still owns
approval, execution, verification).

Adapter contract: `execute`, `stream`, `get_status`, `cancel`, `resume`. `AgentTaskRequest`:
task type, input, read-only context, tool manifest, policy snapshot, model config, prompt
version, deadline, idempotency key, trace context. `AgentTaskResult`: schema-validated output,
evidence, token usage, tool-call summary, status. Common `PlatformEvent`s: agent_started,
model_call, tool_call, tool_result, state_transition, error, agent_finished (with token/cost).

## 5. Primary use case: partner onboarding (`partner_onboarding`)

Engine must allow a second workflow (e.g. `access_request`) by config + new agents (stretch).

1. Intake — persist request; Intake Agent classifies (type, entities, missing info, in scope,
   urgency/escalation).
2. Parallel investigation — Research, Policy, Data agents concurrently, bounded concurrency,
   per-task timeout, read-only.
3. Planning — ordered `ProposedAction`s: type, typed params, evidence refs, risk class,
   expected outcome, dependencies, owner, idempotency key.
4. Validation — deterministic validators first (schema, required fields, permissions, business
   rules, duplicates, allowlist, risk), then advisory Validation Agent. Deterministic wins.
   Invalid → rejected or back to Planning (max 2 refinements).
5. Approval — queue; durable pause (survives restart); per-action approve/reject/request
   changes; TTL expiry and escalation.
6. Execution — via gateway; idempotency keys; bounded retries with exponential backoff +
   jitter; retryable vs non-retryable; per-action timeout.
7. Verification — independent read-back; mismatch → `completed_with_warnings` or `failed` plus
   compensating/escalation record.
8. Reporting — summary from actual execution records only.

Statuses: received, classifying, investigating, planning, validating, awaiting_approval,
awaiting_requester_info, executing, verifying, completed, completed_with_warnings, failed,
cancelled — legal transitions in one module with tests; every transition timestamped + event.

Agents: Intake · Research · Policy (explicit policy vs inferred, with citations) · Data ·
Planning (never executes) · Validation (advisory) · Execution (approved only, via gateway) ·
Reporting. Prompts in `backend/agents/<name>/prompts/vN.md`; version recorded per execution.

## 6. Data, integrations, knowledge

`mock-business-systems` FastAPI service: CRM (partners, orgs, contacts, status, owners,
history), Ticketing, Knowledge base (Markdown policies, BM25 first), Notifications.
Fault injection (timeouts, 500s, malformed JSON, slow, duplicate delivery) via headers or a
control endpoint. Connector interface: `health_check()`, `retrieve(query)`, `execute(action)`.
Seed: ~50 partners, ~30 policies, historical requests, planted traps.

DB entities: users, requests, workflows, workflow_runs, agent_executions, agent_events,
tool_invocations, evidence, proposed_actions, approvals, action_executions, integrations,
feedback, evaluation_runs, evaluation_results, outbox_events, idempotency_keys (unique).
UUIDs, timestamps, explicit status enums, FKs tracing every run to its request.

## 7. Safety invariants

1. No action executes without passing schema, required-field, permission, business-rule, risk,
   approval and idempotency checks in the deterministic policy engine.
2. Risk: low (read-only) auto; medium (internal task create/assign, non-sensitive status) auto
   or approval per config; high (access, financial, external commitments, deletes) always human.
3. Allowlisted tools only; unknown/unavailable tools rejected and logged.
4. Bounded retries; one idempotency key never yields two side effects.
5. Tool I/O logged with secrets and PII redacted.
6. Prompt-injection defense: retrieved/user text is untrusted, delimited and labeled; policy
   engine ignores instructions in data; injection cases in security tests and the eval set.
7. Approvals immutable and attributed (who, when, what they saw, hash); modified actions need
   re-approval.
8. Runs resumable after a crash without duplicate side effects.

## 8. Evaluation & benchmarking

≥100 cases in `data/evaluation/*.yaml` (input, seeded world, expected classification, required
and prohibited actions, expected approvals, final status, rubric). Categories: normal,
incomplete, ambiguous, conflicting, invalid, approval-required, integration-failure,
should-reject, prompt-injection, duplicate-submission. Systems: non-agentic baseline (built
first), ADK, LangGraph, optional CrewAI. Same model, temperature, prompts, tools, data, cases;
document differences. Metrics: task success, classification accuracy, tool-selection accuracy,
argument validity, evidence coverage, policy compliance, unsafe-action rate (executed and
attempted), recovery rate, human-intervention rate, p50/p95 latency, tokens, estimated cost,
schema consistency. N≥3 runs per case; means with bootstrap CIs; per-category breakdowns; say
"no significant difference" when so. Deterministic scoring first; LLM-as-judge only for
subjective fields (versioned, spot-checked). `make eval` + CI smoke subset on the fake LLM;
full live runs stored with config + git SHA. Auto-generate `docs/benchmarks/REPORT.md`.

### 8A. Dataset generation — synthetic only

No external datasets/environments. Seeded generators `scripts/generate_world.py`,
`scripts/generate_eval_cases.py`; `make seed` / `make gen-eval` reproduce byte-for-byte.
World (`data/seed/`): ~50 partners, ~30 policies with stable IDs and explicit checkable rules,
~200 historical tickets, traps in `data/seed/traps.yaml` (missing compliance doc, conflicting
records, duplicate partner, expired cert, contradicting policies, inactive owner,
restricted-region partner). Cases (`data/evaluation/cases/*.yaml`, ≥100, Pydantic-validated,
CI fails on invalid): id, category, split, world_ref, request, requester, attachments, traps,
expect{classification, in_scope, required_actions, prohibited_actions, approval_required,
final_status, required_evidence}, scoring. Distribution: normal ~20 · incomplete/ambiguous ~20
· conflicting ~10 · approval/high-risk ~15 · should-reject ~10 · integration failures ~10 ·
prompt injection ~10 · duplicates ~5. Rules: hand-write ~15 seed cases; expand with templates
and LLM-assisted variation; labels from construction (LLM-suggested labels need human review,
`reviewed: true`); ~30% dev / ~70% frozen test, dataset version/hash per run; injection cases;
`make lint-data`; `docs/benchmarks/DATASET.md` with method, rationale, biases, limitations.

### 8B. Methodology

Final-state scoring; `pass^k` (k=1,3,5); separate policy-compliance scorer; simulated requester
for `awaiting_requester_info` (scripted in CI); weighted sub-checkpoints for partial credit;
deterministic graders first; version dataset/scorer/simulator/model/frameworks per run;
failure taxonomy (wrong tool, bad args, unsupported claim, skipped approval, injection
followed, bad recovery, timeout). State that synthetic results may not transfer.

## 9. Observability

OTel spans: run → stage → agent execution → model call → tool call → connector call with
framework, agent, model, prompt version, tokens, cost, retries, outcome. Prometheus metrics
(workflow counts/latency, approval queue depth, tool errors, cost). Grafana dashboards as code
in `infra/monitoring/`. `agent_events` persisted for the UI trace view. Cost table in config;
cost labeled an estimate.

## 10. Frontend

Pages: Overview, Requests, Workflow detail, Agent execution (trace tree), Approvals (evidence,
diff, risk badge, approve/reject/request changes, history), Integrations (health + test),
Analytics (incl. estimated hours saved with documented, configurable baseline), Framework
benchmarks, Docs. SSE live updates; "replay on the other framework" comparison; empty/loading/
error states; accessible; dark mode. Mobile: responsive + installable PWA, mobile-first
Approvals, optional web push; no native app before DoD; OpenAPI for future clients.

## 11. API (`/api/v1`, OpenAPI, consistent error envelope)

Requests: `POST/GET /requests`, `GET /requests/{id}`. Workflows: `POST /workflows/{id}/run`
(framework, mode, model_config), `GET /workflows/runs/{run_id}`, `POST .../cancel`,
`POST .../replay`, `GET .../events` (SSE). Agents: `GET /agents`, `GET /agents/executions/{id}`.
Approvals: `GET /approvals`, `POST /approvals/{id}/decision`. Integrations: `GET
/integrations`, `POST /integrations/{id}/test`. Analytics: `/analytics/overview`,
`/workflows`, `/framework-comparison`. Evaluations: `POST /evaluations/run`,
`GET /evaluations/{id}`, `GET /evaluations/compare`. Auth: simple JWT/session with roles
`requester`, `reviewer`; seeded demo users.

## 12–13. Layout and roadmap

Layout: `apps/{api,web}`, `backend/{core,domain,orchestration,frameworks,agents,tools,
integrations,approvals,policy,evaluation,observability,database}`, `mock_business_systems/`,
`data/`, `tests/{unit,integration,framework,security,evaluation,e2e}`, `docs/`, `infra/`,
`scripts/`, Makefile (setup, up, down, migrate, seed, test, test-fast, lint, typecheck,
eval-smoke, eval-full, demo).

Phases: 0 scaffolding · 1 non-AI foundation (schemas, state machine, models, mock systems with
faults, world generator, request API, rule-based end-to-end workflow, basic UI) · 2 tool
gateway + policy engine (registry, typed args, permissions, allowlists, risk, idempotency,
redaction, audit, validators, approvals queue, durable pause/resume, hypothesis tests) · 3 LLM
layer + baseline (LLMClient, FakeLLMClient, cost, single-agent baseline, structured output with
retry) · 4 ADK multi-agent · 5 LangGraph adapter + cross-framework equivalence tests · 6
observability (OTel, Prometheus, Grafana, SSE, trace UI) · 7 evaluation (dataset, runner,
scorers, statistics, comparison endpoints, benchmark UI, report) · 8 frontend polish +
analytics + PWA · 9 hardening, deployment, docs (chaos, security tests, README, Mermaid
diagram, API docs, demo script). Stretch only after DoD.

## 14. Definition of Done

Functional: submit → persisted → end-to-end workflow → multiple agents → same workflow via ADK
and LangGraph → validated actions → approvals incl. partial → execution via APIs →
independently verified → full history inspectable. Technical: shared typed contracts, DB
persistence, idempotency, bounded retries, structured logs, tracing, unit/integration/security/
failure tests, crash-recovery test, offline CI with fake LLM, reproducible `docker compose up`.
Evaluation: ≥100 cases; baseline + ADK + LangGraph under controlled conditions with multi-run
stats; latency, cost, success, safety; limitations and negative findings. Product: dashboard,
approval UX (also mobile), searchable history, setup docs, API quick-start, architecture
diagram, demo script.

## 15. Demo scenario

1. Submit cloud-partner onboarding. 2. Classify; Research + Policy + Data in parallel.
3. Planner proposes onboarding tasks, a compliance-doc request, an external notification.
4. Validation detects a missing compliance doc and an evidence-unsupported notification.
5. Reviewer approves the internal task, rejects the notification, requests the missing doc.
6. Execution creates the task via ticketing; verification confirms it. 7. Reporting summary
from real results. 8. Dashboard shows trace, tool calls, approvals, latency, cost. 9. Replay on
the other framework and baseline; compare. 10. (Bonus) kill the worker during step 6 and
recover with no duplicate task.

Positioning: *WorkflowOps demonstrates that different AI orchestration systems can reliably
complete the same operational task inside a controlled, observable, measurable software
platform.* Only claim what is implemented and measured.
