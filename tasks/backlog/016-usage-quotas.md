---
id: 016-usage-quotas
feature: runtime
status: pending
---

# Per-tenant usage quotas

## Migration preflight

Before implementation, inspect the relevant sections of `docs/plan.md`, the governing ADRs, this task, and its directly dependent or consuming tasks. Record:

- target end-state and contracts introduced here;
- legacy code allowed only as a temporary rollback bridge;
- legacy imports, data paths, and behaviors forbidden in new code;
- the task that removes each temporary bridge;
- an architecture test or CI check that enforces the boundary.

## Scope
`usage_counter` table and enforcement so one chatty (or looping) user
can't burn through our Fireworks budget or Google API quotas. Soft
daily caps with a polite in-character reply.

## Acceptance criteria
- [ ] Migration: `usage_counter` (pk(`contact_id`, `date`),
      `llm_tokens` int, `tool_calls` int); RLS pattern per
      `28b0ac108edc`
- [ ] Worker increments `llm_tokens` (from the Pydantic AI usage
      object) and `tool_calls` after each turn — single upsert
- [ ] Turn start: over daily cap → skip the LLM call, send the fixed
      cap message ("limite diário atingido, volta amanhã"), log the
      event
- [ ] Cap values in `app/config.py` (env-tunable), not hardcoded
- [ ] Logfire metric/log per capped turn so abuse is visible

## Out of scope
- Billing, tiered plans, per-user cap overrides
- Rate limiting (per-minute) — daily caps suffice for MVP

## Log
### [PA] 2026-08-05 15:45 — Grooming
Created from `docs/plan.md` Phase E. Depends on 004, 005.
