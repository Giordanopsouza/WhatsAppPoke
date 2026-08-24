# Architecture migration plan

This is the canonical target for the OpenPoke-style runtime migration.
ADRs record why; task files are the independently shippable execution
units. Until task 047 cuts production over, current runtime documentation
in `AGENTS.md` and `docs/deploy.md` remains operationally authoritative.

| Topic | Where |
|---|---|
| Domain terms | [`docs/glossary.md`](glossary.md) |
| Architecture decisions | [`docs/adr/`](adr/) |
| Tables, RLS, tenancy | [`docs/database.md`](database.md) |
| Current Railway runtime | [`docs/deploy.md`](deploy.md) |
| Interactive 3D System Map | [`docs/architecture_3d.html`](architecture_3d.html) |
| Atomic implementation work | [`tasks/NNN-*.md`](../tasks/) |

## Goal

Reduce time-to-first-useful-message and tool latency without copying
OpenPoke's single-user file storage or in-memory global state.

The target keeps Twilio, Postgres tenancy, background jobs, retries for
scheduled work, and code-enforced confirmation. It changes conversation
orchestration:

```text
Twilio webhook
  verify + persist inbound + 200
  start an in-process interaction event
    Interaction Agent
      send_message_to_user(...)  -> persist + Twilio immediately
      dispatch_execution(goal)   -> detached Execution run

Execution completes
  persist result event
  re-enter Interaction with latest conversation context
  send_message_to_user(...)
```

There is no classifier, tool-less `agent_chat`, hardcoded ack, or
conversation `agent_turn` job in the target.

## Agent model

### Interaction Agent

The Interaction Agent is the only WhatsApp speaker. Every inbound user
message and every completed background/execution event passes through it.
It owns only orchestration tools:

- `send_message_to_user` — reserve an idempotent outbound sequence,
  persist, send through Twilio, and record delivery metadata;
- `dispatch_execution` — create an isolated detached execution run;
- `request_integration` — mint the existing contact-scoped Composio
  Connect link;
- `cancel_execution` — request cooperative cancellation;
- `wait` — finish without another visible message.

The Pydantic result is a typed internal outcome (`done`,
`waiting_execution`, or `silent`), never text that the caller sends.
This makes `send_message_to_user` the one outbound contract and prevents
the runtime from duplicating a final response.

There is no fixed `"já tô nisso."`. The Interaction Agent decides whether
a slow operation benefits from a short, natural status message. A hard
runtime fuse allows at most five visible messages per Interaction run.

### Execution Agent

One reusable Pydantic Agent definition runs isolated executions on
demand. It never sends WhatsApp messages. Each run receives:

- one explicit goal from Interaction;
- contact-scoped dependencies and timezone;
- only the owned tools for integrations active on that contact;
- shared usage limits and an execution timeout.

The first release does not create named persistent agents or a roster.
“Spawn” means the deterministic `dispatch_execution` service starts an
async task and records its lifecycle; it is not a third LLM agent.

At most two executions may be active per contact. A deterministic
dedupe key prevents the same goal from spawning twice. New user messages
may continue while executions run. Completion, failure, timeout, or
cancellation becomes an internal event that reacquires the per-contact
Interaction lock and is interpreted against the latest history.

Conversation work is intentionally best-effort after the webhook 200.
An api restart may abandon an in-memory Interaction or Execution task.
Persisted stale runs become `abandoned`; they are not automatically
replayed. Scheduled/background work keeps durable jobs.

## Tool architecture

Composio remains the managed-auth provider:

- `user_id = str(contact_id)`;
- one active connected account per contact + toolkit in the first release;
- existing signed `/connect/{toolkit}` flow remains;
- `integration.external_account_id` selects the Composio account.

MCP, remote tool schemas, and the Composio allowlist/deny-list are removed.
Owned business tools provide their own Pydantic schemas, validation,
compact results, telemetry, and confirmation rules. Their implementation
calls the Composio authenticated proxy with fixed endpoints; no generic
HTTP/proxy tool is exposed to either model.

Existing local business operations (Tavily search, tasks, reminders, and
automations) also live on Execution. They do not use the Composio proxy.
Interaction keeps only conversation/orchestration tools.

The first integration slice is Gmail + Google Calendar:

```text
Gmail: search_emails, get_email, create_email_draft,
       stage_send_email, execute_confirmed_email_send

Calendar: list_calendars, list_events, get_event,
          stage_create_event, execute_confirmed_event_create
```

Writes are controlled in code with `pending_action`. A send/create is
staged with its exact payload, source turn, hash, expiry, and status. It
can execute only after an explicit confirmation arrives in a later
WhatsApp inbound. Prompt instructions are not the security boundary.

## Concurrency and outbound idempotency

Interaction events are serialized per contact across api replicas with a
short-lived Postgres advisory lock. The lock covers one Interaction run,
not the lifetime of detached executions. Execution result events use the
same lock before re-entering Interaction.

Every visible tool call reserves `(interaction_run_id, sequence)` before
calling Twilio. Retries or repeated model tool calls return the existing
outbound instead of sending twice. If the model fails before the first
outbound, retry it once; after any outbound side effect, do not rerun the
whole Interaction. If no message was sent and the retry fails, send one
fixed idempotent fallback.

## Reminder and automation

Two product concepts remain separate:

1. **Reminder** — deterministic text stored now and sent at `due_at`.
   No LLM at fire. The existing durable `reminder_due` job remains.
2. **Automation** — an RRULE + timezone + natural-language goal that
   may use owned tools. A durable `automation_due` job runs Execution,
   stores the result, and re-enters Interaction.

Recurring automations compute and persist `next_run_at`. After downtime,
run at most one catch-up occurrence, then advance to the next future
occurrence. Unattended sensitive work stages `pending_action` and asks
the user; it never silently bypasses confirmation.

There is no platform-pushed daily briefing (ADR 0017). Reminder,
automation, and action proactive flows use approved Twilio Content
Templates outside the 24-hour WhatsApp service window.

## Persistence target

Task 039 owns exact DDL, indexes, RLS, and model naming. Conceptually:

- `execution_run` — goal, toolkit scope, status, dedupe key, timestamps,
  compact result/error, and cancellation state;
- `execution_event` — internal completion/failure/timeout event and
  processing status, separate from user-visible messages;
- outbound idempotency fields/table — Interaction run + sequence and
  delivery state around the Twilio side effect;
- `automation` — goal, RRULE, timezone, `next_run_at`, state, catch-up
  policy, and proactive template metadata;
- `job` — keeps background kinds; `agent_turn` is drained and retired.

Every new tenant row has `contact_id`, the existing FK convention, RLS,
and revoked Data API grants. Internal execution data never appears in
the chat transcript unless Interaction explicitly sends it.

## Delivery order

| Order | Task | Outcome |
|---|---|---|
| 1 | 039 | Execution/event/outbound persistence primitives |
| 2 | 040 | Single Interaction Agent + idempotent multi-message output |
| 3 | 041 | Detached Execution lifecycle, limits, timeout, and re-entry |
| 4 | 042 | Composio authenticated-proxy foundation; no MCP |
| 5 | 043 | Owned Gmail read/draft/send-confirm tools |
| 6 | 044 | Owned Calendar read/create-confirm tools |
| 7 | 045 | RRULE Automation schema, tools, and worker scheduling |
| 8 | 046 | Briefing migration and proactive Twilio templates |
| 9 | 047 | Interaction-only cutover; remove classifier/ack/MCP and `agent_turn` |
| later | 014 | Contact-scoped working memory after runtime stabilization |
| gate | 015 | Golden conversations and tool/security evals |

One task means one branch and one PR. Do not stack these changes on a
long-lived migration branch.

## Cutover cleanup

Task 047 makes Interaction the sole ordinary inbound conversation runtime.
The cleanup migration discards obsolete queued `agent_turn` rows, removes the
job kind and its partial index, then deletes the classifier, ack, MCP, and
legacy persona modules. Background jobs never stop during this cutover.

## Release gates

Proposed launch gates, to validate against production samples:

- chat time-to-first-visible-message p95 under 3 seconds;
- first natural status bubble p95 under 4 seconds when emitted;
- Gmail/Calendar execution result p95 under 30 seconds;
- webhook p95 under 2 seconds;
- zero duplicate outbounds for one run/sequence;
- zero sensitive writes without a claimed `pending_action`;
- zero owned tools attached for a disconnected toolkit;
- every Execution ends in a terminal state, including timeout/abandoned.

Current Logfire evidence (2026-08-01 through 2026-08-15) shows
`agent_tool` / `ask_execution` p95 near 169–170 seconds over 23 samples,
while the current api chat model span is about 0.77 seconds p95. The
migration must improve tool execution, not merely remove the classifier.

## Explicit non-goals

- no Redis, RabbitMQ, SQS, Temporal, or fourth Railway service;
- no named agent roster in the first release;
- no generic authenticated HTTP tool;
- no full Composio toolkit catalog;
- no automatic replay of lost conversation tasks after api restart;
- no working-memory implementation before the new runtime stabilizes;
- no Drive, Sheets, Notion, Trello, or ClickUp owned tools in this batch.

## Decision records

- ADR 0014 — Interaction + detached Execution runtime.
- ADR 0015 — owned tools over Composio managed auth proxy.
- ADR 0016 — deterministic reminders and agentive automations.
- ADR 0017 — no product-level daily briefing.
- ADR 0007 — Twilio remains the only transport.
- ADR 0011 — reminder fire remains deterministic.
