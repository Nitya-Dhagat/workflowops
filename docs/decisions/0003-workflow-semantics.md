# 0003 — Workflow semantics: rejections, gating, final status, idempotency

**Status:** accepted · 2026-11-20

## Context
The brief lists the statuses but not how policy rejections, empty plans or partial outcomes
map onto them. These choices determine evaluation labels, so they must be explicit.

## Decision
- **Policy rejections end in `failed` with `status_reason` prefixed `rejected:`**; system
  errors use the prefix `error:`. (There is no separate "rejected" status in the brief.)
- **Platform-owned hard gates** run after investigation, using facts the platform fetched
  itself (never agent output): partner not found, restricted region (POL-ONB-007), partner not
  in `prospect` status (POL-ONB-001). These stop the run before planning, identically for every
  framework.
- Out-of-scope classification → `failed` (`rejected: out of scope`). Onboarding intent without
  an identifiable partner → `awaiting_requester_info`.
- **Final status** (`engine._final_status`): any failed/unverified action → `completed_with_
  warnings` if something verified, else `failed`; a verified compliance-doc request →
  `awaiting_requester_info`; a verified escalation → `completed_with_warnings`; expired or
  skipped actions → `completed_with_warnings`; nothing executed → `completed_with_warnings`;
  otherwise `completed`. A reviewer rejecting an action is a normal outcome (`completed`).
- **Approval batching:** auto-approved (medium) actions wait until every pending approval in
  the run is decided, then all approved actions execute together in dependency order. An
  action whose dependency did not execute becomes `skipped`.
- **Idempotency key** = hash(request id, action type, canonical params). Re-running the same
  request cannot duplicate a side effect; replays for comparison must use a fresh request
  against a reset world.
- **Refinement loop:** only planner errors (schema, allowlist, evidence, duplicate) trigger
  re-planning (max 2). Business-rule rejections are final — they are the policy working.
- `request_changes` currently marks the action `changes_requested` and does not re-plan; the
  re-planning loop is Phase 2 work.

## Consequences
Evaluation case labels (Phase 7) are derived from these rules. Changing them bumps the dataset
version.
