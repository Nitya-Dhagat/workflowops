# 0004 — Authentication deferred to Phase 2; identity passed explicitly

**Status:** superseded by ADR-0006 (auth implemented 2026-11-27) · 2026-11-20

## Context
The brief asks for simple JWT/session auth with `requester` and `reviewer` roles. Phase 1's
goal is the non-AI end-to-end foundation.

## Decision
Phase 1 has no authentication. Requester and reviewer IDs are passed in request bodies and
validated against the seeded `users` table; approval decisions are refused (403) unless the ID
belongs to an active `reviewer`. JWT auth with the two roles is implemented in Phase 2, after
which the body-supplied IDs are replaced by the authenticated principal.

## Consequences
Until Phase 2, anyone who can reach the API can act as any seeded user. Acceptable only because
the system runs locally against simulated systems; do not deploy publicly before Phase 2.
