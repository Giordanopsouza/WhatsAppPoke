---
id: 033-deterministic-reminder-ping
feature: reminders
status: done
---

# Deterministic reminder ping (drop reminder agent)

## Scope
When a `reminder_due` job fires, send the stored `reminder.body`
(optionally with a fixed prefix) via Twilio — no LLM compose. Remove
`reminder_agent` / `compose_reminder_reply`. Keep the existing send
split: free-form inside the 24h customer-service window, Utility
Content Template (`TWILIO_REMINDER_CONTENT_SID`) outside it.

## Why
Task **013** ran a short tool-less agent turn so the ping had “gg’s
voice.” That adds latency and Gemini cost for a message that is already
fully known at `set_reminder` time. Briefing knocks (031 / ADR 0010)
already send without an LLM; reminders should match that pattern.

## Acceptance criteria
- [x] `handle_reminder_due` builds the outbound text from `reminder.body`
      only (deterministic; no history load for compose). A single shared
      helper (e.g. `format_reminder_ping(body) -> str`) owns any fixed
      prefix/wrapping so the handler stays dumb.
- [x] `reminder_agent`, `REMINDER_INSTRUCTIONS`, and
      `compose_reminder_reply` are deleted from `app/agent/loop.py`;
      worker re-exports / imports no longer reference them.
- [x] In-window → `send_text`; out-of-window → `send_content_template`
      with `body_variable` = the deterministic text (same as today).
- [x] Claim / release / persist outbound / noop-on-cancelled behaviour
      unchanged.
- [x] Tests updated: no mocks of `compose_reminder_reply`; assert the
      sent body equals the formatted `reminder.body`.
- [x] `docs/glossary.md` reminder row no longer says “short agent turn
      (gg tone)”; note deterministic body instead. Append a short note
      on task **013** log or a one-paragraph ADR addendum if the team
      wants the reversal recorded (optional; glossary is required).

## Out of scope
- Changing Utility template copy / `ContentSid` / Meta re-approval.
- Recurring / snooze / edit-reminder tools.
- Making briefing compose at knock time.
- Replacing `in_customer_service_window` or moving category logic.

## Depends on
- **013** (done): `reminder` table, `reminder_due` handler, Utility SID.

## Log
### [PA] 2026-08-13 21:07 — Grooming
Product question: reminder fire does not need an agent — body is known
at schedule time. Scoped as one atomic task (handler + delete
compose agent + tests + glossary). No split needed: no schema, no new
Twilio assets, no second PR surface.

### [SWE] 2026-08-13 21:15 — Implementation
Dropped `reminder_agent` / `compose_reminder_reply`. Fire path is
`format_reminder_ping(reminder.body)` then the existing in-window /
Utility send split. Glossary + ADR 0011 record the reversal of 013.

### [SWE] 2026-08-13 21:22 — Done
Merged PR #23 and deployed to Railway. Moved file to `tasks/done/`.

