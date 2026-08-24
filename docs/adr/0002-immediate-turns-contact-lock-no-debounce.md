0002. Immediate agent turns with per-contact lock (no sliding debounce)
Status: Accepted
Date: 2026-08-06

Context
The original task 003 proposed a 3-second sliding
debounce per contact: coalesce rapid inbound messages by bumping
`job.run_at = now() + 3s` until the user goes quiet, then run one
agent turn. That optimizes for "3 rapid messages → exactly one reply"
and avoids out-of-order overlapping replies.

In practice the silence wait stacks with claim + LLM + send latency.
A single short message ("oi") would always pay ~3s before reasoning
even starts, pushing p95 webhook→reply well above the product target.
Cancel-and-restart (start immediately, abort the LLM if more inbound
arrives mid-turn) would preserve both speed and single-reply bursts,
but needs cancellable async inference, cross-process "new message"
detection, and a generation token so a stale turn never sends—too much
complexity for the current sync `agent.run` + dual api/worker shape.

Decision
Do **not** use a sliding debounce (or any artificial silence window)
before agent turns.

1. Enqueue `agent_turn` with `run_at = now()` (immediate). Keep the
   existing partial unique index: at most one **pending** `agent_turn`
   per contact (insert-or-coalesce; do not delay `run_at`).
2. While a turn is **running**, new inbound may create a new pending
   job (unique index only covers `pending`). That job runs after the
   current turn finishes.
3. The worker holds a Postgres advisory lock keyed on `contact_id` for
   the whole turn so one contact never runs concurrent turns (safe with
   multiple worker replicas). Different contacts stay parallel.
4. Fire Z-API typing/presence when a turn starts so the user sees
   activity during LLM time. Presence errors must not fail the turn.

Cancel-and-restart mid-turn remains a possible later upgrade if
double-replies on bursts become a real product complaint; it is not
part of task 003.

This supersedes the 3-second sliding debounce proposed in task 003.
Task file: `tasks/003-contact-lock-and-typing.md`
(replaces `003-debounce-and-contact-lock`).

Consequences
Positive:
- Single-message latency is claim + LLM + send only—no fixed silence tax.
- Same-contact turns are serialized; no overlapping/out-of-order replies
  from concurrent workers.
- Typing presence covers perceived wait during reasoning.
- No new infrastructure (still Postgres jobs + advisory locks).

Negative / tradeoffs:
- A burst that arrives while a turn is already running can produce a
  second follow-up reply after the lock releases (ordered, full history),
  instead of one coalesced reply for the whole burst.
- Messages that land in the tiny window while a job is still pending
  (before claim) still coalesce via the unique index, but without
  delaying `run_at`—so the worker may claim before the full burst lands.
- Advisory locks must use a stable key derivation from `contact_id` and
  be released reliably (including on failure paths).

Rejected alternatives:
- Sliding 3s debounce — too much idle latency for every turn.
- Cancel-and-restart — correct UX long-term, deferred as too complex now.
- Tiny debounce (e.g. 300ms) — still adds lag; weak burst benefit.
