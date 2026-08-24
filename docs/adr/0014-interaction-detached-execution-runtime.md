0014. Interaction agent with detached execution runs
Status: Accepted
Date: 2026-08-15

Context
The classifier + api chat + ack + queued agent path in ADR 0013 has
three different response contracts. Tool work waits behind a job claim,
the hardcoded ack is repetitive, and nested `ask_execution` keeps the
WhatsApp turn open until the tool agent finishes. Production traces from
2026-08-01 through 2026-08-15 show api chat at about 0.77s p95 but
`agent_tool` / `ask_execution` near 169–170s p95 over 23 samples.

OpenPoke demonstrates a useful control-flow split: an Interaction Agent
owns the conversation, Execution Agents run asynchronously, and results
return as internal messages. Its literal implementation is not suitable:
it is single-user, stores state in files, uses process-global roster and
batch objects, and loses asyncio tasks on restart.

The product accepts best-effort conversation work after Twilio receives
200. It does not accept cross-tenant state, duplicate WhatsApp sends, or
prompt-only confirmation for sensitive actions.

Decision
Use one Pydantic AI Interaction Agent as the only WhatsApp speaker.
Remove the classifier, separate `agent_chat`, and hardcoded ack after
the migration cuts over.

The webhook continues to validate the Twilio signature, persist inbound,
deduplicate by provider message id, and return 200 before any LLM call.
After 200, the api starts an in-process Interaction event. Conversation
events do not use the Postgres `agent_turn` queue.

Interaction owns orchestration tools only:

- `send_message_to_user` is the sole visible outbound contract;
- `dispatch_execution` creates a detached execution run;
- `request_integration` mints the existing managed-auth link;
- `cancel_execution` requests cooperative cancellation;
- `wait` ends without another visible message.

The agent output is a typed internal state (`done`,
`waiting_execution`, or `silent`), not user-visible text. A runtime fuse
allows at most five `send_message_to_user` calls per Interaction run.
The agent decides whether slow work needs a natural status message; no
fixed ack is sent.

Use one reusable Execution Agent definition. Each dispatch creates an
isolated `execution_run`, with one goal, contact-scoped dependencies,
and only the tools connected for that contact. This is the “spawn”
operation; there is no Spawner Agent and no named roster in the first
release.

At most two executions may be active for one contact. A deterministic
dedupe key prevents duplicate goals. Executions run as api asyncio tasks,
persist lifecycle and result, and have a 90-second default timeout.
Completion, failure, timeout, or cancellation creates an internal event
that re-enters Interaction with the latest conversation history.
Execution never calls Twilio directly.

New user messages may be processed while an Execution runs. Interaction
events, including result re-entry, acquire a Postgres advisory lock per
contact for one Interaction run only. The detached Execution does not
hold that lock.

`send_message_to_user` reserves an idempotency key derived from
`interaction_run_id` and sequence before the Twilio side effect. A model
failure may retry once only if the run has not sent a visible message.
After any visible side effect, the whole Interaction is not replayed.
If both pre-send attempts fail, one fixed idempotent fallback is sent.

Persist execution runs and internal events for audit, dedupe, and active
run context, but do not use them as a durable conversation queue. On
process restart, stale running rows become `abandoned`; they are not
replayed or automatically announced. This loss is an explicit tradeoff.

Background jobs remain durable. The worker continues to own reminders,
automations, and integration notifications.

Consequences
Positive:
- every inbound has one conversational brain and one WhatsApp voice;
- simple chat no longer pays a classifier call;
- slow execution no longer blocks later user conversation;
- natural multi-message replies replace the fixed ack;
- execution lifecycle is observable without making it a queue;
- api/worker keep Postgres tenancy and Twilio transport boundaries.

Negative:
- api restart can lose an in-flight conversation or execution;
- result events may arrive after the user changed subject, so
  Interaction must contextualize against current history;
- the api now owns detached tasks and needs lifecycle cleanup;
- database-backed per-event locking holds a connection across an
  Interaction run;
- multi-message output can increase Twilio cost, bounded by the fuse.

Rejected alternatives
- Keep classifier + ack + `agent_turn`: preserves the latency and split
  response contract being replaced.
- Hold the contact lock for the full Execution: prevents conversation
  while slow work runs and makes “detached” meaningless.
- Let Execution speak directly: loses persona, context, confirmation,
  and one outbound authority.
- Named persistent agent roster: adds lifecycle and tenant complexity
  before a concrete product need.
- Durable queue for all conversation executions: explicitly traded away
  for lower interaction latency.
- Process-global batch manager copied from OpenPoke: unsafe across
  contacts, replicas, triggers, and deploys.

Supersedes
- ADR 0013 in full.
- ADR 0012 for the synchronous nested `ask_execution` lifecycle.
- ADR 0002 only for conversation `agent_turn` scheduling and its
  full-turn lock. ADR 0002 remains applicable to durable background jobs.

Implementation
Tasks 039, 040, 041, and 047.
