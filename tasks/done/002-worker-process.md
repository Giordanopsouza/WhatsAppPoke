---
id: 002-worker-process
feature: runtime
status: done
---

# Worker process

## Scope
`python -m app.worker` — asyncio loop that claims jobs with
`FOR UPDATE SKIP LOCKED`, executes per-kind handlers, retries failures
with exponential backoff, and dead-letters after `max_attempts`. The
webhook stops using `asyncio.create_task` and enqueues instead.

## Acceptance criteria
- [x] Claim: `SELECT ... WHERE status='pending' AND run_at <= now()
      ORDER BY run_at LIMIT 1 FOR UPDATE SKIP LOCKED`, then mark
      `running` + `locked_at`
- [x] `agent_turn` handler is the current `agent_turn()` logic moved
      out of `app/main.py` (dedup, history load, LLM, send, persist)
- [x] Failure: `attempts += 1`, back to `pending` with
      `run_at = now() + backoff(attempts)`; `attempts >= max_attempts`
      → `dead` + error log
- [x] Crash recovery: `running` jobs with `locked_at` older than 10 min
      are re-queued at startup
- [x] Webhook handler enqueues a job and returns 200 as today; no
      in-process turn execution remains
- [ ] Manual: kill the worker mid-turn, restart → the job is retried
      and the user still gets exactly one reply

## Out of scope
- Debounce and per-contact locking (task 003)
- `reminder_due` / `integration_notify` handlers (tasks 008, 013)
- Pydantic AI migration of the turn itself (task 004)

## Log
### [PA] 2026-08-05 15:45 — Grooming
Created from `docs/plan.md` Phase A. Depends on 001.
### [SWE] 2026-08-06 12:25 — Start
Implementing `app.worker` claim loop + webhook enqueue.
### [SWE] 2026-08-06 12:35 — Complete
Worker + queue helpers shipped. Webhook persists inbound then enqueues;
worker claims with SKIP LOCKED, runs `agent_turn`, retries with exponential
backoff, dead-letters at max_attempts, recovers stale locks on startup.
Queue smoke-tested against Supabase. Manual kill/restart check left for Tester.
