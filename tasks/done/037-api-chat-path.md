---
id: 037-api-chat-path
feature: agent
status: done
---

# Fast chat on the api (no job)

## Migration preflight

Before implementation, inspect the relevant sections of `docs/plan.md`, the governing ADRs, this task, and its directly dependent or consuming tasks. Record:

- target end-state and contracts introduced here;
- legacy code allowed only as a temporary rollback bridge;
- legacy imports, data paths, and behaviors forbidden in new code;
- the task that removes each temporary bridge;
- an architecture test or CI check that enforces the boundary.

## Scope
After Twilio 200, run classify + chat on the **api** process. Chat
never writes `job`. Only `agent` enqueues today’s `agent_turn`. No
ack yet (038). Revises `api ≠ worker` for this chat path only.

## Why
Queue claim is extra wait on “oi”. The user-visible reply is
`send_text`, not the webhook 200. Persist inbound, 200, then
in-process Gemini on the api.

## Architecture (this slice)

```text
Twilio → api: persist inbound, 200
  background (no job, no advisory lock):
    STOP / briefing opt-in → fixed reply (moved off worker)
    briefing_cue → enqueue agent_turn
    classifier
      chat  → persona, same system_prompt.md, tools=[] → send → persist
      agent → enqueue agent_turn (worker run_turn as today)
```

## Acceptance criteria
- [x] Webhook still: verify, persist, 200. Duplicate
      `provider_message_id` still drops. Empty body still no LLM.
- [x] After 200, in-process work on the api (not inside the Twilio
      request). Chat does **not** insert `job`.
- [x] Briefing STOP / opt-in-only stay deterministic on the **api**
      (Q7); classifier never sees them. Worker copies can remain as a
      safety net or be dropped if tests move.
- [x] `briefing_cue` (pending knock) skips classifier → enqueue
      `agent_turn` (038 will ack first).
- [x] `chat`: `run_turn` / persona with **no** `LOCAL_TOOLS` and **no**
      `ask_execution`. Same `system_prompt.md`. Persist outbound.
- [x] Chat Gemini error → existing fallback
      `desculpa, tive um problema… tenta de novo em instantes.`
      (Q16). Process death after 200 → no retry (Q12).
- [x] `agent`: enqueue `agent_turn` only (coalesce rules unchanged).
      Worker `run_turn` unchanged in this task.
- [x] No per-contact advisory lock on the api (Q11). Double-reply on
      overlap is accepted.
- [x] ADR **0013** is the spec (already accepted). Implement it: classifier + chat on api, worker for tool turns. Keep `docs/glossary.md` / `AGENTS.md` in sync if this PR touches runtime wording.
- [x] Tests: chat inbound → no `job` row, persona ran without tools;
      agent inbound → `agent_turn` enqueued; STOP still fixed reply;
      webhook 200 does not await Gemini. No live Gemini in CI.

## Out of scope
- `já tô nisso.` (038).
- Advisory lock on api (explicitly rejected).
- Redis / LISTEN (018).
- Audio transcription (028) — when it ships, dispatch through this
  path, not a parallel enqueue-everything path.
- Nested exec / allowlist changes.

## Depends on
- **036** (classifier module).

## Log
### [PA] 2026-08-14 16:05 — Grooming
Grill: chat + classifier on api; worker only for tools. Persist+200
then in-process. Chat is not a job. No api lock. Q16 fallback on
live chat errors. Ack deferred to 038.

### [PA] 2026-08-14 16:10 — ADR 0013
Decision record landed: `docs/adr/0013-chat-on-api-agent-on-worker.md`.
This task implements the runtime; do not re-litigate the split.

### [SWE] 2026-08-14 16:30 — Chat on api
Webhook persists + 200, then BackgroundTasks dispatch. STOP / opt-in-only
fixed on api; briefing_cue and `agent` enqueue `agent_turn`; `chat` runs
`run_chat_turn` (same system_prompt.md, no tools). Worker STOP left as
safety net. No api lock. Ack still 038.

### [SWE] 2026-08-14 16:43 — Ship
Commit, PR, merge to `main`.
### [PA] 2026-08-15 15:22 — Superseded architecture
ADR 0014 replaces the split chat/agent route with one Interaction Agent.
Legacy behavior remains deployable for task 047 canary rollback.
