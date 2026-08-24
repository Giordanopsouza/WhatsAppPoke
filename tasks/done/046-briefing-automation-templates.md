---
id: 046-briefing-automation-templates
feature: automations
status: done
---

# Migrate briefing to Automation and proactive templates

## Migration preflight

Before implementation, inspect the relevant sections of `docs/plan.md`, the governing ADRs, this task, and its directly dependent or consuming tasks. Record:

- target end-state and contracts introduced here:
  * Briefing scheduling runs as an `Automation` row (`name='Briefing Matinal'`, RRULE weekday 08:00 local) waking via `automation_due -> Execution -> Interaction`.
  * `briefing_state` retained as user preference state (opt-out, unanswered knocks count, pending knock, cadence) synchronized with `Automation.status` and `rrule`.
  * Proactive Content Template contracts defined in `app.core.config` and `app.transport.twilio_wa` for briefing (static quick-reply), reminder (`{{1}}`), automation ready (`{{1}}`), and action confirmation (`{{1}}`).
  * Interaction outbound uses `send_text` in-window and approved Content Templates out-of-window.
- legacy code allowed only as a temporary rollback bridge:
  * `outbound_sweep` and `outbound_due` worker jobs remain for unmigrated contacts until task 047 canary cutover.
- legacy imports, data paths, and behaviors forbidden in new code:
  * `agent_turn` / classifier / MCP path for migrated contacts.
  * Direct WhatsApp sending from `automation_due` or Execution.
  * Embedding un-minted connect links in scheduled templates.
- the task that removes each temporary bridge:
  * Task 047 (canary cutover and cleanup).
- an architecture test or CI check that enforces the boundary:
  * `tests/test_briefing_automations.py::test_briefing_architecture_boundaries` and `tests/test_automations.py::test_architecture_boundaries`.

## Scope
Move daily briefing scheduling onto Automation while preserving current
cadence/opt-out behavior and WhatsApp service-window compliance. Define
the explicit Twilio Content Template contracts used by briefing,
reminder, and generic automation notifications.

## Acceptance criteria
- [x] Existing eligible contacts receive or are mapped to one briefing
      Automation with weekday 08:00 local RRULE without duplicate knocks.
- [x] Migration preserves briefing opt-out, pending reply, unanswered
      count, daily/weekly cadence, and last-knock semantics. Document
      whether `briefing_state` remains as preference state or is folded
      into Automation.
- [x] Briefing due work follows
      `automation_due → Execution → Interaction`; no MCP and no
      `agent_turn` is required for a migrated contact.
- [x] A response to a briefing template becomes a normal Interaction
      inbound with the pending briefing/automation context injected.
- [x] Config defines approved Content SIDs and variable contracts for:
      briefing-ready, deterministic reminder, and automation-ready /
      action-needs-response.
- [x] Outside the 24-hour customer-service window, only approved Content
      Templates are used. Inside the window, free-form output still goes
      through Interaction's idempotent outbound tool.
- [x] Connect links are minted only after user response, never embedded in
      a scheduled template before their 10-minute lifetime.
- [x] STOP/briefing opt-out and re-opt-in stay deterministic and affect
      only briefing unless the user explicitly changes another automation.
- [x] Template/category errors are logged with provider id/error code and
      do not leak message bodies. Retry policy distinguishes permanent
      Meta rejection from transient Twilio failures.
- [x] Tests cover in/out-of-window delivery, migration idempotency,
      existing opt-out, daily→weekly cadence, response consume, template
      variables, and no duplicate briefing Automation.
- [x] Manual plan lists required Meta/Twilio template approvals before
      production enablement.

## Manual Template Approval Plan (Meta / Twilio Content Template Builder)

Before production rollout of proactive notifications:
1. **Briefing Knock Template (`TWILIO_BRIEFING_CONTENT_SID`):**
   - Category: `MARKETING`
   - Language: `pt_BR`
   - Type: `twilio/quick-reply`
   - Body: `Bom dia! Seu briefing de hoje está pronto.`
   - Action Button: `Ver agora` (id `ver_agora`)
   - Approval: Required by Meta prior to proactive sends.
2. **Deterministic Reminder Template (`TWILIO_REMINDER_CONTENT_SID`):**
   - Category: `UTILITY`
   - Language: `pt_BR`
   - Type: `twilio/text`
   - Body: `{{1}}`
3. **Automation Completion Template (`TWILIO_AUTOMATION_CONTENT_SID`):**
   - Category: `UTILITY`
   - Language: `pt_BR`
   - Type: `twilio/text`
   - Body: `{{1}}`
4. **Action Confirmation Template (`TWILIO_ACTION_CONTENT_SID`):**
   - Category: `UTILITY`
   - Language: `pt_BR`
   - Type: `twilio/text`
   - Body: `{{1}}`

## Out of scope
- Important-email watcher.
- Dynamic arbitrary LLM copy inside an unapproved template.
- Removing the old briefing scheduler before canary validation (047).

## Depends on
- 045.
- 043 and 044 for briefing sources.
- ADR 0016.

## Log
### [PA] 2026-08-15 15:22 — Grooming
Unify scheduling without pretending WhatsApp is web chat. Proactive work
still obeys the 24-hour window and approved-template boundary.
### [PA] 2026-08-16 00:00 — Implementation
Implemented Task 046: migration of briefing to Automation, Content Template contracts, privacy logging, and in/out-of-window delivery.
