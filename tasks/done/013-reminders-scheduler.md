---
id: 013-reminders-scheduler
feature: features
status: done
---

# Reminders via delayed jobs

## Scope
`reminder` table + agent tools to set / list / cancel. Scheduling is a
delayed `reminder_due` job (`run_at = due_at`) — no cron, no Redis; the
worker's claim on `run_at` (task 001) is the scheduler.

**Source of truth is the row.** The job is only a wake-up; the handler
re-reads the reminder and no-ops unless `status = active`.

**Edit = cancel + set again.** No `edit_reminder` / reschedule tool. The
agent cancels the old row and creates a new one (new job). An orphan
wake-up for a cancelled row is fine — check-at-fire drops it.

**Fire goes through the agent.** The handler does not send `body` raw.
It runs a short agent turn so the outbound has gg's voice, then sends.
Persist the outbound like any other reply.

**Outside the 24h WhatsApp window is in scope.** Free-form `send_text`
only works inside the customer service window. Outside it, send an
approved Utility Content Template (Twilio `ContentSid`) whose variable
carries the agent-composed text. Inside the window, free-form is fine
(and Meta does not charge Utility fees in-window).

```text
set_reminder → INSERT reminder + INSERT job(reminder_due, run_at=due_at)
                 same transaction; payload = {reminder_id}

reminder_due → contact_turn_lock
            → re-read row; if not active → complete job (no-op)
            → short agent turn (reminder body as cue) → reply text
            → send: free-form if in 24h window, else Utility template
            → mark reminder sent + persist outbound message
            → complete job
```

## Acceptance criteria
- [x] Migration: `reminder` (`contact_id` FK, `body`, `due_at`,
      `sent_at` null, `status` check in (`active`, `sent`,
      `cancelled`), timestamps); RLS pattern per `28b0ac108edc`
- [x] Tools: `set_reminder(body, due_at)`, `list_reminders`,
      `cancel_reminder(index)`
- [x] `set_reminder` parses relative times ("tomorrow 9am", "in 2
      minutes") in the contact's tz (reuse `parse_tool_datetime` /
      clock injection); rejects past `due_at`; inserts row + enqueues
      `reminder_due` with `run_at = due_at` and
      `payload = {reminder_id}` in **one transaction**
- [x] `list_reminders` concise, active first, due times in contact tz
- [x] `cancel_reminder` takes 1-based `list_reminders` index; marks
      row `cancelled` (job left alone); agent asks which number when vague
- [x] No edit/reschedule tool — system prompt / tool docs say "cancel
      then set again" is how you change a reminder
- [x] Worker handler for `JobKind.REMINDER_DUE` (enum already exists):
      - takes `contact_turn_lock` (same as `agent_turn` /
        `integration_notify`)
      - re-reads reminder; if missing or not `active` → complete, no
        WhatsApp send
      - otherwise runs a short agent turn that composes the ping in
        gg's voice from `body` (+ recent history as context)
      - sends via Twilio (`twilio_wa`, never Z-API): free-form inside
        the 24h window; approved Utility template outside it
      - marks `sent` + `sent_at`, persists outbound `message`, then
        completes the job
- [x] Idempotent on retry / crash-after-send: a second attempt must not
      double-ping (status `sent` and/or outbound dedupe keyed on
      `reminder_id` — mirror `integration_notify`'s
      `outbound_exists_since` pattern)
- [x] Utility Content Template created + approved in Twilio/Meta; env
      holds `ContentSid` (document in `.env.example` + `docs/deploy.md`)
- [x] Worker downtime: reminders due during an outage fire on recovery
      (past `run_at` jobs are claimed)
- [x] Fixture-based tests for tools + handler (active / cancelled /
      already-sent; in-window vs out-of-window send path mocked)
- [ ] Manual: "me lembra em 2 minutos de alongar" → WhatsApp ping in
      gg's tone ~2 min later

## Out of scope
- Recurring reminders
- Snooze
- Native edit/reschedule tool (cancel + set is the product path)
- Linking a reminder to a `task` row (user may create both; no FK)

## Log
### [PA] 2026-08-05 15:45 — Grooming
Created from `docs/plan.md` Phase D. Depends on 002, 009 (contact.tz).

### [PA] 2026-08-09 21:14 — Grooming
Rewrote after design pass: edit = cancel+set; fire through agent (gg
tone); out-of-window sends via Utility template (in scope, not deferred);
Twilio-only; list + fuzzy cancel; same-txn dual-write; advisory lock +
send idempotency. Dropped vague "edited" AC and Z-API leftover.

### [A] 2026-08-09 22:15 — Implementation
Migration + ORM + tools + `handle_reminder_due` (claim-before-send,
tool-less compose agent, free-form vs ContentSid). Env/docs for
`TWILIO_REMINDER_CONTENT_SID`. Tests in `test_reminders.py` +
`test_worker_jobs.py`. Manual AC still open until template + deploy.

### [A] 2026-08-09 21:40 — Simplify
Dropped fuzzy match; `cancel_reminder(index)` only.

### [SWE] 2026-08-13 21:15 — Reversal (task 033)
Fire no longer runs an agent turn. The ping is `reminder.body` via
`format_reminder_ping` (ADR 0011). Send split (free-form vs Utility
template) is unchanged.
