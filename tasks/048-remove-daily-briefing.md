---
id: 048-remove-daily-briefing
feature: briefing
status: in-progress
---

# Remove product-level daily briefing

## Migration preflight

Inspected ADRs 0010 and 0016, tasks 031 / 046 / 047, `app/services/briefing.py`,
`app/db/briefing.py`, `app/api/dispatch.py`, `app/worker/loop.py`, and the
outbound handlers.

- **Target end-state:** no weekday morning knock, no `briefing_state`, no
  `outbound_sweep` / `outbound_due`, no seeded `Briefing Matinal` Automation.
  Inbound is webhook 200 → `dispatch_inbound` → Interaction with no briefing
  intercept. Worker claims only `reminder_due`, `automation_due`, and
  `integration_notify`. Gmail/Calendar remain on-demand owned tools.
  Contact-created Automations remain.
- **Live path today:** worker boot seeds `outbound_sweep` (~15 min, all
  contacts) → `outbound_due` sends the Marketing template → tap **Ver agora**
  is a normal inbound. `STOP` / `para` / `parar o briefing` are handled in
  dispatch before Interaction.
- **Dormant path today:** Alembic `b3c4d5e6f7a8` inserted one
  `Briefing Matinal` Automation per contact and set `next_run_at`, but
  **did not** insert `automation_due` jobs. `ensure_briefing_automation` is
  unused in runtime. Do not finish that migration; delete the product
  feature instead.
- **Temporary rollback bridge:** none. Cut over in this PR. Drain pending
  `outbound_sweep` / `outbound_due` in the same Alembic revision that
  retires the kinds, so a rolling deploy cannot claim a handler that no
  longer exists.
- **Forbidden in new code:** `briefing_state`, briefing opt-out/in phrases,
  `send_briefing_template`, `TWILIO_BRIEFING_CONTENT_SID`,
  `JobKind.OUTBOUND_SWEEP` / `OUTBOUND_DUE`, seed-on-boot sweep, and any
  Interaction branch that treats `"briefing"` in `internal_event_summary`
  as a special outbound template.
- **Bridge removal:** this task. 047 left sweep/due in place on purpose;
  048 is the deletion.
- **Architecture/CI boundary:** extend `tests/test_runtime_cleanup.py` (or
  a sibling) so pytest fails if briefing modules, outbound kinds, or
  `TWILIO_BRIEFING_CONTENT_SID` reappear. Delete
  `tests/test_briefing.py` and `tests/test_briefing_automations.py`.

## Scope

Delete the product-level daily briefing (knock + preference cache + seeded
Automation) from runtime, schema, config, tests, and docs. Leave generic
Automation, deterministic reminders, and owned Gmail/Calendar tools.

## Acceptance criteria

- [x] Worker no longer seeds or handles `outbound_sweep` / `outbound_due`.
      Boot does not insert a tenant-less job.
- [x] `dispatch_inbound` only loads recent history as needed and runs
      Interaction. No `get_briefing_state`, consume-pending, or STOP/para
      intercept. `para` is a normal user message.
- [x] Interaction does not read `briefing_state`, does not inject
      `briefing_matinal_pendente`, and does not call `send_briefing_template`.
      Out-of-window proactive sends use automation/action/reminder templates
      only.
- [x] Delete `app/services/briefing.py`, `app/db/briefing.py`,
      `app/database/models/briefing_state.py`,
      `app/worker/handlers/outbound_sweep.py`,
      `app/worker/handlers/outbound_due.py`, and their exports.
- [x] Alembic revision (one):
      1. delete pending/running `job` rows with kind `outbound_sweep` or
         `outbound_due`;
      2. cancel (or delete) `automation` rows named `Briefing Matinal`
         and any pending `automation_due` for those ids;
      3. drop table `briefing_state` and enum/check `briefing_cadence`;
      4. retire job kinds `outbound_sweep` / `outbound_due` from
         `ck_job_kind` and drop their partial unique indexes;
      5. make `job.contact_id` NOT NULL (today it is nullable only for
         sweep) and drop `ck_job_sweep_contact`.
- [x] `JobKind` and worker `HANDLERS` match the remaining kinds:
      `reminder_due`, `integration_notify`, `automation_due`.
- [x] Remove `twilio_briefing_content_sid` from `app/core/config.py` and
      `TWILIO_BRIEFING_CONTENT_SID` from `.env.example` and `docs/deploy.md`.
      Reminder / automation / action SIDs stay.
- [x] Strip briefing copy from `app/agent/interaction_prompt.md`.
- [x] Docs in the same PR: ADR recording the removal (supersede 0010;
      amend 0016 so briefing is no longer a product Automation);
      `docs/glossary.md` (drop **briefing knock** / **cadence**; rewrite
      **outbound_sweep** / **job** kinds); `docs/database.md`;
      `AGENTS.md` (remove `app/services/briefing.py` from the module table
      and sweep/due from worker invariants).
- [x] Tests: briefing suites gone; worker/runtime/dispatch tests no longer
      mock briefing; architecture test forbids the deleted modules and kinds.
      `uv run pytest` passes.
- [ ] Manual: after deploy, no 08:00 knock; a weekday inbound is a normal
      Interaction turn; existing Gmail/Calendar connections and user-created
      reminders/automations still work. Twilio Marketing template may remain
      unused in the Twilio console (do not require deleting it this PR).

## Out of scope

- Removing Automation as a domain object, `automation_due`, or the
  create/list/pause tools.
- Removing Gmail/Calendar owned tools or connect links.
- Removing reminder fire or `TWILIO_REMINDER_CONTENT_SID`.
- Asking the user to recreate a morning digest as a custom Automation
  (they can, later; this task does not seed one).
- Deleting the approved Twilio Marketing template in Meta/Twilio.

## Depends on

- 031, 046, 047 (briefing exists in the Interaction-only runtime).
- ADR 0016's reminder vs automation split stays; only the briefing
  product decision is reversed.

## Log

### [PA] 2026-08-17 16:45 — Grooming

Product no longer wants a platform-pushed morning briefing. The live
cost is the 15-minute all-contact sweep plus an extra Interaction turn
on **Ver agora**. The 046 Automation rows are inert schedule clutter.
Delete both in one PR; do not complete the sweep → `automation_due`
cutover that 047 deferred.

### [SWE] 2026-08-17 17:10 — Remove daily briefing

Deleted the weekday knock, `briefing_state`, outbound sweep/due jobs,
seeded `Briefing Matinal` automations, briefing opt-out intercept, and
the Marketing template config. Inbound is webhook 200 → Interaction;
worker kinds are reminder, automation, and integration notify.
