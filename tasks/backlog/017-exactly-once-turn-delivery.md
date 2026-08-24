---
id: 017-exactly-once-turn-delivery
feature: runtime
status: pending
---

# Exactly-once turn delivery: close the duplicate-reply windows

## Migration preflight

Before implementation, inspect the relevant sections of `docs/plan.md`, the governing ADRs, this task, and its directly dependent or consuming tasks. Record:

- target end-state and contracts introduced here;
- legacy code allowed only as a temporary rollback bridge;
- legacy imports, data paths, and behaviors forbidden in new code;
- the task that removes each temporary bridge;
- an architecture test or CI check that enforces the boundary.

## Scope
Close the three paths where a single inbound message can produce two
agent replies: the send-before-persist window, stale recovery without a
fencing token, and stale recovery running only at startup. This is what
`docs/plan.md` Phase A actually promises ("kill the worker mid-turn,
restart → user still gets exactly one reply") and what the unchecked
manual criterion in `tasks/done/002-worker-process.md` was meant to
verify.

## Context
Audit of the queue found the claim path correct — `claim_job`
(`app/db.py`) uses a single `UPDATE … WHERE id = (SELECT … FOR UPDATE
SKIP LOCKED) RETURNING`, so read and status flip are atomic and two
workers can never claim the same row. Retry/backoff/dead-letter exist.
The duplication risk is entirely downstream of the claim.

### A. Send happens before the outbound row is persisted
`app/worker.py` `_run_agent_turn` calls `send_text()` and only then
`insert_outbound_message()` + commit. The sole guard against a
re-executed turn is `history[-1]["role"] == "assistant"`, which depends
on that insert having landed. If the insert raises (pooler drops the
connection — the case `pool_pre_ping` exists for), the exception
propagates → `_mark_failed` → re-queue with backoff → retry loads a
history ending in `user` → **second LLM call, second WhatsApp reply**.
No process crash required.

### B. No fencing token on the job lock
`recover_stale_jobs` re-queues on `locked_at` age alone, with no signal
about whether the owning worker is alive. `locked_at` is stamped at
claim time, and the job sits in `running` while blocked on the
per-contact advisory lock in `handle_agent_turn` — so the 10-minute
stale clock starts before work does. A legitimately slow or
lock-blocked turn can be re-queued and claimed by a second worker while
the first still runs; the contact advisory lock serializes them but does
not prevent the second execution.

`complete_job` and `fail_job` filter on `status == RUNNING` only, never
on owner. The original worker therefore marks `DONE` a job that now
belongs to another worker, and the second worker's `UPDATE` becomes a
**silent no-op** — the queue's state stops reflecting reality.

### C. Stale recovery runs only at startup
`run_worker` calls `recover_stale_jobs` once before the loop. No
periodic reaper: a long-lived worker never recovers orphans, its own or
a dead replica's. The partial unique index only covers `pending`, so an
orphaned `running` row blocks nothing and is simply never retried. The
only recovery trigger is a restart — exactly when B is most dangerous.
Compounding this, `main()` installs no SIGTERM handler, so a deploy
mid-turn strands the job in `running` for ≥10 minutes and then feeds it
into path A.

## Acceptance criteria
- [ ] Outbound message row is persisted in the same transaction that
      records the send, or an idempotency key makes a repeated
      `send_text` for the same job a no-op — a failed persist can no
      longer cause a second reply
- [ ] Turn replay is keyed on the job, not on a history heuristic:
      re-running a claimed job that already sent never calls the LLM
      again (drop or demote `history[-1] == "assistant"` to a backstop)
- [ ] `job` gains an owner/fencing column (`locked_by` uuid or
      `lock_token`), set at claim; migration mirrors the RLS + grant
      hardening in `183da715dd33`
- [ ] `complete_job` / `fail_job` / `_dead_letter` filter on owner as
      well as `status`, and a lost-ownership `UPDATE` logs loudly
      instead of no-opping silently
- [ ] `recover_stale_jobs` runs periodically inside the worker loop, not
      only at startup; stale threshold is measured from work start, not
      claim time (or the advisory-lock wait moves outside `running`)
- [ ] SIGTERM/SIGINT handler drains the in-flight job (bounded) and
      releases its claim before exit, so a deploy does not strand a job
      in `running`
- [ ] Tests (`tests/test_queue.py`, new): concurrent claim yields
      disjoint jobs; persist-failure after send does not double-reply;
      a re-queued job cannot be completed by its previous owner;
      SIGTERM mid-turn leaves the job claimable
- [ ] Manual: kill the worker mid-turn, restart → the user still gets
      exactly one reply (closes the open item in 002)

## Out of scope
- Worker concurrency / parallel turn execution (task 018) — this task
  makes duplication impossible *before* we multiply the workers that
  could trigger it
- Backoff jitter and a reprocessing surface for `dead` rows
- Replacing the Postgres queue with anything else (see `docs/plan.md`
  deferred list)

## Log
### [PA] 2026-08-06 — Grooming
Created from a queue audit against the SKIP LOCKED reference model.
Claim path and retry/dead-letter verified correct; the three findings
here are all downstream of the claim. Depends on nothing; should land
before 018 (worker concurrency), since more workers raises the hit rate
on B.
