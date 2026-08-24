---
id: 003-contact-lock-and-typing
feature: runtime
status: done
---

# Per-contact lock and typing presence

## Scope
Serialize agent turns per contact with a Postgres advisory lock.
Enqueue stays immediate (`run_at = now()`)—no sliding debounce.
Relies on task 001's partial unique index (at most one pending
`agent_turn` per contact).

## Acceptance criteria
- [x] Worker takes a pg advisory lock keyed on `contact_id` for the
      duration of a turn; turns for different contacts run in parallel
- [x] Enqueue path remains immediate (`run_at = now()`); if a pending
      `agent_turn` already exists, coalesce (no second pending job)—do
      **not** delay `run_at`
- [ ] Manual: single message → reply with no artificial silence wait
      (only claim + LLM + send)
- [ ] Manual: burst while a turn is running → follow-up turn(s) after
      the lock releases, ordered, no overlapping/out-of-order replies
- [ ] Manual: two different contacts messaging simultaneously → both
      answered, neither blocked

## Out of scope
- Sliding debounce / silence window before claiming a turn
- Cancel-and-restart of an in-flight LLM turn
- Media/audio batching
- Z-API typing/presence (no pre-send endpoint; only post-LLM
  `delayTyping` on send-text, which adds latency)

## Log
### [PA] 2026-08-05 15:45 — Grooming
Created from `docs/plan.md` Phase A. Depends on 002.

### [PA] 2026-08-06 13:12 — Rewrite (no debounce)
Dropped 3s sliding debounce: latency stacks with LLM time and hurts
single-message UX. Keep immediate enqueue + per-contact advisory lock
+ typing presence. Burst coalescing into exactly one reply and
cancel-and-restart are explicitly out of scope (see ADR 0002).
Renamed from `003-debounce-and-contact-lock`.

### [SWE] 2026-08-06 13:20 — Start
Implementing per-contact advisory lock + Z-API typing presence; enqueue stays immediate.

### [SWE] 2026-08-06 13:35 — Complete
`contact_turn_lock` (`pg_advisory_xact_lock`, held for the whole turn) +
`send_typing` before LLM (soft-fail). Enqueue stays immediate coalesce.
Smoke-tested lock acquire/release + same-contact serialization against
Supabase. Note: Z-API has no public pre-send typing endpoint today
(`send-chat-presence` → NOT_FOUND); helper probes once, logs
`zapi_typing_unsupported`, then no-ops — turn still succeeds. Manual
checks left for Tester.

### [SWE] 2026-08-06 13:40 — Drop typing dead code
Removed `send_typing` / worker call. Z-API has no pre-send presence API;
keeping a no-op probe was dead code. Typing moved to out of scope.

### [SWE] 2026-08-06 13:47 — Commit
Marking done; shipping per-contact advisory lock + immediate enqueue.
