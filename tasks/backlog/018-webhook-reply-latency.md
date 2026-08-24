---
id: 018-webhook-reply-latency
feature: runtime
status: pending
---

# Cut webhook → reply latency (DB connect + queue wait)

## Migration preflight

Before implementation, inspect the relevant sections of `docs/plan.md`, the governing ADRs, this task, and its directly dependent or consuming tasks. Record:

- target end-state and contracts introduced here;
- legacy code allowed only as a temporary rollback bridge;
- legacy imports, data paths, and behaviors forbidden in new code;
- the task that removes each temporary bridge;
- an architecture test or CI check that enforces the boundary.

## Scope
Reduce p95 time from Z-API webhook receipt to WhatsApp `send` by
killing repeated Supabase connect cost and idle poll delay. Logfire
trace `019fda4920ba04c97f72a1c5a2f2c324` showed ~4.9s inside
`job.execute` where ~2.3s was new DB connects (lock / history /
persist / complete) plus ~1.2s queue wait after a 1.25s webhook —
LLM was only ~1.5s.

## Acceptance criteria
- [ ] Worker claim loop keeps a warm DB connection (or pooled checkout
      that does not re-`connect` every idle poll); empty polls are a
      cheap claim query, not connect + UPDATE
- [ ] A single agent turn uses at most two DB connections for lock +
      history_load + persist + complete_job (no four separate
      connect-per-phase pattern)
- [ ] Revisit `pool_pre_ping` / pool settings against Supabase
      transaction pooler (`:6543`); after warm-up, Logfire should not
      show a ~0.4s `connect` span on every checkout
- [ ] Queue wake path: either Postgres `LISTEN/NOTIFY` from webhook
      enqueue → worker, or equivalent so pending jobs start without
      waiting a full idle sleep + slow claim
- [ ] API webhook path stays one session; pool warmed at process
      start so first upsert is not a cold connect
- [ ] Manual: send a WhatsApp message, confirm in Logfire that
      webhook → `send` drops vs today’s p50 (~13s paired / ~5s on a
      quiet single turn); `connect` spans per turn are rare/near-zero
      after warm-up

## Out of scope
- Model / Fireworks changes (outliers are provider variance)
- Debounce or contact-lock redesign (see ADR 0002)
- Moving off Supabase pooler
- Persisting outbound before `send` (user-visible latency ends at send)

## Log
### [PA] 2026-08-06 23:41 — Grooming
Created from Logfire latency investigation on
`starter-project` / trace `019fda4920ba04c97f72a1c5a2f2c324`. Priority
order agreed: warm/reuse DB connections → fewer sessions per turn →
wake worker on enqueue → then model tweaks if still needed.
