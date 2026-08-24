0016. Deterministic reminders and agentive automations
Status: Accepted
Date: 2026-08-15

Context
The product has two different scheduled intents:

- “remind me to take medicine at 20:00” stores the exact text to send;
- “every weekday at 08:00, check my calendar” requires tools and
  reasoning when the schedule fires.

Treating both as one agentive reminder wastes model calls and expands
the authority of simple pings. Treating both as deterministic cannot
support recurring research or connected-app workflows.

ADR 0011 already made reminder fire deterministic. ADR 0010 implemented
daily briefing as specialized `briefing_state` plus a worker-seeded
sweep; that product path is removed by ADR 0017. The Interaction/Execution
runtime still needs a general background event path without moving
scheduled work into api asyncio tasks.

Decision
Keep Reminder and Automation as separate domain concepts.

Reminder:

- stores the final `body` and one `due_at`;
- uses the existing delayed `reminder_due` Postgres job;
- sends deterministically without an LLM;
- remains cancel + recreate rather than edit;
- uses the approved reminder Content Template outside the WhatsApp
  24-hour customer-service window.

Automation:

- stores a contact-scoped natural-language goal;
- stores an iCalendar RRULE, contact timezone, status, and `next_run_at`;
- may declare the connected toolkits required by its goal;
- wakes through a durable `automation_due` job;
- runs the reusable Execution Agent with owned tools;
- persists the execution result and re-enters the Interaction Agent;
- never lets Execution send WhatsApp directly.

The worker computes and persists the next occurrence. After downtime it
runs at most one catch-up occurrence and advances `next_run_at` to the
next future occurrence. It does not replay every missed interval.
Worker claim, retries, stale recovery, and per-contact serialization
remain Postgres-backed.

Unattended automation cannot bypass sensitive-action confirmation.
If the result proposes send/create/delete behavior that requires
confirmation, it creates `pending_action`; Interaction asks the user,
and a later inbound may claim and execute it.

Daily briefing is not a product Automation. ADR 0017 removes the
platform knock, `briefing_state`, and seeded `Briefing Matinal` rows.
Contact-created Automations remain; Gmail/Calendar stay on-demand owned
tools. Outside the 24-hour window, proactive sends use approved Twilio
Content Templates for reminder, automation, and action — not a briefing
Marketing template.

Consequences
Positive:
- simple reminders stay fast, cheap, and exact;
- recurring tool work gets a general durable scheduler;
- all agentive background results return through the same Interaction
  voice and confirmation rules;
- one worker and one Postgres queue still cover all scheduled work.

Negative:
- Automation adds a table, job kind, RRULE calculation, and more states;
- proactive WhatsApp delivery requires approved templates and category
  compliance;
- background Execution may complete outside the service window and
  cannot always send free-form content immediately;
- a generic natural-language goal needs evals to prevent surprising tool
  selection.

Rejected alternatives
- Make every reminder agentive: unnecessary cost and authority.
- Keep a specialized briefing scheduler: duplicates the Automation
  scheduler and result re-entry path. The product later dropped the
  morning knock entirely (ADR 0017).
- Run scheduler loops in the api: conversation tasks are best-effort,
  scheduled work is not.
- Add pg_cron, Redis, Temporal, or another service: the existing job
  table and worker already provide the needed durability.
- Replay all missed RRULE occurrences: can spam the user and duplicate
  actions after downtime.
- Allow pre-authorized sensitive automation writes in the first release:
  confirmation semantics and revocation are not designed yet.

Supersedes
- None. ADR 0017 supersedes ADR 0010 and this ADR's product-briefing
  Automation decision.

Retains
- ADR 0011 in full for deterministic reminder fire.
- ADR 0002 queue/claim/lock mechanics for background jobs.

Implementation
Tasks 045 and 046. Task 048 / ADR 0017 reverse the briefing product
decision without removing Automation as a domain object. See `docs/plan.md`.
