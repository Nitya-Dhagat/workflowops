# 0006 — Hardening: authentication, execution grants, DB run leases, infra-aware evaluation

**Status:** accepted · 2026-11-27 (supersedes ADR-0004)

## Context
The first interview-prep review listed four top weaknesses: no authentication (identities sent in
request bodies), a gateway that trusted a caller-supplied "approved" status, an in-process lock
that could not stop two workers from advancing the same run, and an evaluation that counted
provider/network errors as model failures.

## Decision
1. **Authentication.** Users have salted scrypt password hashes (cost stored in each hash; hashing
   runs off the event loop). `POST /api/v1/auth/login` issues an HS256 JWT (12 h, `WFO_JWT_SECRET`;
   random per process if unset). Every `/api/v1` router requires a token; identity and role are
   re-read from the database on each request. Request creation and approval decisions take the
   user from the token; the body fields `requester_id` / `reviewer_id` were removed, so they can't
   be spoofed. Only reviewers may decide (403 otherwise). Demo accounts are seeded with
   `WFO_DEMO_PASSWORD` (synthetic users only).
2. **Execution grants.** Before a side effect the executor calls `issue_grant`, which re-reads the
   action and its approval from the database, requires a reviewer's approval with a matching hash
   for actions that need a human, and returns a 10-minute HMAC-signed grant. The gateway verifies
   signature, age, idempotency key and params hash; a gateway without a secret executes nothing.
   Grants are minted and verified in one worker, so a per-process key is enough.
3. **DB run leases.** `advance()` claims the run with a conditional `UPDATE … WHERE lease is free,
   ours, or expired` and renews it before every stage. A worker that can't claim waits briefly
   (the holder may be about to pause) and then leaves the run to the holder.
4. **Infra-aware evaluation.** The LLM client retries transport errors; quota, overload and
   network failures are excluded from quality metrics and re-run by the next daily batch.

## Consequences
- The public demo now needs a sign-in; the login page shows the synthetic demo password.
- Tokens are stateless: there is no server-side revocation before expiry (12 h).
- A worker stalled for longer than the 5-minute lease can lose the run to another worker; it then
  stops at its next stage boundary instead of racing.
