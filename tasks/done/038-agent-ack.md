---
id: 038-agent-ack
feature: agent
status: done
---

# Deterministic ack before the tool agent

## Migration preflight

Before implementation, inspect the relevant sections of `docs/plan.md`, the governing ADRs, this task, and its directly dependent or consuming tasks. Record:

- target end-state and contracts introduced here;
- legacy code allowed only as a temporary rollback bridge;
- legacy imports, data paths, and behaviors forbidden in new code;
- the task that removes each temporary bridge;
- an architecture test or CI check that enforces the boundary.

## Scope
On the `agent` path: send hardcoded `já tô nisso.`, persist it, then
enqueue `agent_turn`. Worker runs today’s `run_turn` and sends the
real reply. One status line, then the answer.

## Why
Tool turns wait on exec. The user should see a status bubble from the
api before the worker starts. Not an LLM. Not a third agent.

## Architecture (this slice)

```text
classifier → agent  (or briefing_cue)
  send_text("já tô nisso.")     # api, persist
  enqueue agent_turn            # after Twilio accepts; retry insert once
  worker run_turn               # ack stays in history; last user is the prompt
  send_text(reply)
```

## Acceptance criteria
- [x] Constant `já tô nisso.` (no “on it”, no model). Api sends it
      **before** enqueue. If enqueue fails, retry the insert once.
- [x] If Twilio rejects the ack, still enqueue (Q17). Work > UX.
- [x] Persist ack as outbound. Duplicate ack on retry: skip a second
      send of that exact body; do **not** treat it as “already
      replied.”
- [x] Worker first attempt: history may end with the ack. `run_turn`
      uses the last user line as the prompt (no strip helper).
- [x] Worker retry (`attempts > 0`): if last outbound is the ack
      constant, still run `run_turn`. If last outbound is a real
      reply, skip (existing already-replied).
- [x] No persona prompt about the ack — it is a normal outbound row.
- [x] `briefing_cue` uses this same ack + enqueue path.
- [x] Tests: agent path sends ack then inserts job; ack-then-crash
      retry still produces one real reply; chat path (037) still no
      ack. No live Twilio/Gemini in CI.
- [x] ADR 0013 / glossary: **ack** is a fixed outbound, not a chat
      speaker and not a job kind.

## Out of scope
- Changing classifier prompts (036) or moving chat back to the
  worker (037).
- Advisory lock on api.
- Token streaming.
- New broker.

## Depends on
- **037** (api already classifies and enqueues `agent` without ack).

## Log
### [PA] 2026-08-14 16:05 — Grooming
Grill: ack is `já tô nisso.`; send then enqueue; persist; retries
still run the agent if last outbound is the ack. Prompt so the
worker does not repeat it. Q17: ack send fail still enqueues.

### [PA] 2026-08-14 16:10 — ADR 0013
Ack rules are in `docs/adr/0013-chat-on-api-agent-on-worker.md` §4.

### [SWE] 2026-08-14 16:45 — Start
Ack then enqueue on the agent / briefing_cue path; worker strips
trailing ack so retries still run `run_turn`.

### [SWE] 2026-08-14 16:55 — Implemented
`ACK_BODY` in `app/agent/ack.py`. Api `_ack_and_enqueue`: send +
persist, skip duplicate body, Twilio fail still enqueues, insert
retry once. Worker strips trailing acks; already-replied ignores
the ack constant. Persona injector tells the model not to repeat it.
Chat path unchanged. 169 tests passed.

### [SWE] 2026-08-14 16:52 — Simplify
Leave ack in history; drop strip helper and system-prompt injector.
`run_turn` already takes the last user line as the prompt.

### [SWE] 2026-08-14 16:55 — Ship
Commit, PR, merge to `main`.
### [PA] 2026-08-15 15:22 — Superseded architecture
ADR 0014 removes the fixed ack. Interaction may send a natural status
message through the idempotent outbound tool; task 047 removes this code.
