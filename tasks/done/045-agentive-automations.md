---
id: 045-agentive-automations
feature: automations
status: pending
---

# RRULE agentive automations

## Migration preflight

Inspected `docs/plan.md` (Reminder vs Automation, `automation_due` →
Execution → Interaction), ADR 0014 (Execution never speaks; worker owns
durable background work), ADR 0016, ADR 0011 (reminder fire stays
deterministic), this task, and consumer task 046 (briefing migration).

- **Target end-state:** `automation` is a contact-scoped RRULE + timezone +
  goal. The existing worker claims `automation_due`, runs the shared
  Execution lifecycle, re-enters Interaction, then advances
  `next_run_at` to the next *future* occurrence (at most one catch-up).
  Owned Execution tools create/list/pause/resume/cancel automations.
  Reminder tools and `reminder_due` are unchanged.
- **Legacy allowed as a temporary rollback bridge:** none for this slice.
  Briefing still uses `briefing_state` + `outbound_sweep` until 046.
  Conversation `agent_turn` / classifier / ack remain until 047.
- **Forbidden in new code:** worker/Execution sending free-form WhatsApp;
  MCP or `agent_turn` for automation fire; treating a reminder as an
  agentive RRULE; replaying every missed occurrence; pre-confirming
  `pending_action`; pg_cron / extra broker / api asyncio scheduler.
- **Bridge removal:** 046 migrates briefing onto Automation; 047 drains
  `agent_turn` and removes classifier/ack/MCP. This task adds no bridge
  that those later tasks must delete.
- **Architecture check:** `tests/test_automations.py::test_architecture_boundaries`
  and `test_reminder_tools_are_not_automation_jobs` assert the worker fire
  path has no `send_text`, Interaction re-entry is the speaker, and
  reminder vs automation enqueue distinct job kinds.

## Scope
Add Automation as a contact-scoped recurring goal, scheduled durably by
the existing Postgres worker. Reminder remains a separate deterministic
feature.

## Acceptance criteria
- [x] Alembic migration + ORM model for `automation`: `contact_id`,
      name/goal, RRULE, timezone, required toolkits, status,
      `next_run_at`, catch-up metadata, last-run fields, and timestamps.
- [x] New `automation_due` job kind requires `contact_id`; at most one
      pending wake-up exists per active automation.
- [x] RRULE parsing rejects invalid/unbounded inputs, resolves in the
      contact timezone, and persists UTC `next_run_at`.
- [x] Worker handler claims one due automation, starts an Execution run
      through the shared lifecycle, and re-enters Interaction with the
      result. Worker/Execution never sends user-visible free-form text
      directly.
- [x] After success or terminal failure, scheduler advances to the next
      future occurrence. After downtime it executes at most one catch-up,
      never all missed occurrences.
- [x] Owned Execution tools create/list/pause/resume/cancel automations.
      A plain reminder request continues to use existing reminder tools
      and `reminder_due`.
- [x] Unattended sensitive work creates `pending_action` and asks through
      Interaction; no automation can pre-confirm a write in this release.
- [x] Worker retry/stale/dead behavior is explicit and idempotent. A
      retried wake-up does not create duplicate Execution runs.
- [x] New table and queries follow contact FK, RLS, revoked Data API, and
      indexed due-scan conventions.
- [x] Tests cover timezone/DST, monthly/weekday RRULEs, one catch-up,
      duplicate job delivery, pause/cancel, disconnected required app,
      sensitive action staging, and reminder/automation distinction.
- [x] `docs/database.md` and `docs/glossary.md` define Automation.

## Out of scope
- Briefing migration and proactive templates (046).
- Pre-authorized sensitive writes.
- Natural-language RRULE parsing outside the agent tool arguments.
- New scheduler process, pg_cron, or broker.

## Depends on
- 039.
- 041.
- Gmail/Calendar task required by the automation's goal (043/044).
- ADR 0016.

## Log
### [PA] 2026-08-15 15:22 — Grooming
Reminder is stored copy; Automation is a scheduled goal. Both use the
existing worker, but only Automation wakes Execution and Interaction.

### [DEV] 2026-08-15 22:00 — Implementation
Schema, RRULE helper, `automation_due` worker path, Execution CRUD tools,
and tests. Catch-up is one missed occurrence then jump to the next future
UTC `next_run_at`. Sensitive writes still stage `pending_action`.
