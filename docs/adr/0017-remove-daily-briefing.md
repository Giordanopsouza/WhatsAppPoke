0017. Remove product-level daily briefing
Status: Accepted
Date: 2026-08-17

Context
ADR 0010 added a weekday 08:00 Marketing-template knock via a worker-seeded
`outbound_sweep` / `outbound_due` pair and `briefing_state`. ADR 0016 then
made briefing a product Automation (`Briefing Matinal`) while keeping the
sweep and preference cache. The live cost is a 15-minute all-contact sweep
plus an extra Interaction turn on **Ver agora**. Seeded Automation rows
never received `automation_due` jobs, so they were schedule clutter.
The product no longer wants a platform-pushed morning digest.

Decision
Delete the product-level daily briefing in this release. No rollback bridge.

- Worker boot does not insert a tenant-less job. Remaining kinds are
  `reminder_due`, `automation_due`, and `integration_notify`.
- Inbound is webhook 200 → `dispatch_inbound` → Interaction. `para` /
  `STOP` are ordinary user messages, not briefing opt-out.
- Interaction does not read `briefing_state`, inject a pending-knock cue,
  or send a briefing Content Template. Out-of-window proactive sends use
  automation, action, or reminder templates only.
- Gmail and Calendar stay on-demand owned tools. Contact-created
  Automations stay. Users may later create their own morning digest;
  the platform does not seed one.
- The Twilio Marketing template may remain unused in the console.

Consequences
Positive:
- No 15-minute all-contact sweep or 08:00 knock.
- `job.contact_id` is always required.
- One inbound path with no briefing intercept.
- Reminder vs Automation split in ADR 0016 is unchanged.

Negative / tradeoffs:
- Contacts who relied on the weekday knock lose it unless they create
  an Automation themselves.
- The approved Marketing template is unused until someone deletes it
  in Twilio/Meta.

Rejected alternatives:
- Finish the 046 cutover (`outbound_sweep` → `automation_due` for
  Briefing Matinal): keeps an unsolicited morning product we no longer
  want.
- Keep `briefing_state` as a preference cache without knocks: dead
  schema for a removed feature.
- Ask users to recreate a morning digest in this release: out of scope.

Supersedes
- ADR 0010 in full.
- ADR 0016 only for the product decision that daily briefing is a
  platform Automation. Reminder vs Automation remains.

Implementation
Task 048.
