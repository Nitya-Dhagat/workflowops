# Phase 2 — Tool gateway + policy engine hardening (plan)

Phase 1 already shipped minimal versions of the gateway, catalog, policy engine, approvals and
executor. Phase 2 hardens them and closes the gaps listed below.

## Work items
1. **Auth (ADR-0004 follow-up):** JWT login for seeded demo users; `requester`/`reviewer`
   roles as FastAPI dependencies; remove body-supplied `reviewer_id`.
2. **Execution grant:** `ToolGateway.execute_action` loads the action + approval from the DB
   itself (or receives a signed grant minted only by the approvals module) instead of trusting
   a caller-supplied status.
3. **Request changes loop:** `changes_requested` sends the plan back to Planning with reviewer
   feedback (counts toward the 2-refinement bound); modified actions get new approvals.
4. **Approval escalation:** expiry escalates to the compliance reviewer (POL-ESC-002) via an
   escalation ticket; background sweeper for expirations + stale-lease recovery.
5. **Read retries:** bounded retries with backoff for read tools in the gateway (currently a
   failing read fails the run).
6. **Intake duplicate detection:** use `requests.dedupe_hash` to flag duplicate submissions.
7. **Property tests:** hypothesis tests for gateway allowlists (any agent × any tool), redaction
   (no email/phone survives), idempotency (N concurrent executions of one key → one effect).
8. **Security tests** (`tests/security/`): injection in request text, attachments and partner
   documents; authz (requester cannot decide approvals; unknown tools denied and logged).
9. Verify `docker compose up` and the Postgres test run once Docker is available.

## Acceptance checks
All of the above covered by tests; safety invariants 1–8 each mapped to at least one named
test in `CLAUDE.md`; CI green on SQLite and Postgres.
