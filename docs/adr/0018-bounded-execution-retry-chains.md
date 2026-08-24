0018. Bounded Execution retry chains
Status: Accepted
Date: 2026-08-17

Context
ADR 0014 made every terminal Execution result re-enter Interaction so the
sole WhatsApp speaker could interpret and communicate it. On 2026-08-17, a
single inbound WhatsApp trace created 320 sequential Execution runs over
about four hours. Each terminal result re-entered Interaction; Interaction
still exposed `dispatch_execution`; the model dispatched a new run for the
same work. Two overlapping traces made 3,775 Gemini calls before prepaid
credits were exhausted.

The existing Pydantic AI `request_limit=6` is scoped to one agent run. It is
not a budget for the chain created by result re-entry. The active-run dedupe
also correctly stops applying once a prior run becomes terminal, but that
behavior is unsafe for an internally generated continuation.

Decision
An Execution owns recoverable retries for its one goal. Interaction owns
only the user-facing terminal response.

- One user-initiated dispatch creates one `execution_run`. Recoverable
  provider/model failures may retry the goal within that same run, with a
  persisted attempt counter, bounded exponential backoff, and a configurable
  default maximum of three attempts.
- Only explicitly classified transient failures (for example provider 429,
  503, and execution timeout) may retry. Validation, authorization,
  unavailable integration, cancellation, and user-input failures are
  terminal.
- A run emits exactly one terminal `execution_event` and therefore schedules
  at most one Interaction result re-entry. Intermediate retry failures do
  not create events or re-enter Interaction.
- An Interaction invoked by an internal `execution.*` result may send a
  final response or remain silent, but cannot call `dispatch_execution`.
  This is enforced by the tool implementation/runtime contract, not prompt
  text. A new user inbound remains able to dispatch work.
- The single run's attempt cap composes with its per-agent request cap. With
  defaults, a logical user request can consume at most three Execution agent
  runs, each subject to `request_limit=6`; it cannot renew that allowance by
  result re-entry.
- Persist the attempt count and attach the execution id, attempt number, and
  terminal status to Logfire spans. Alerting/monitoring must make a run with
  abnormal attempt or model-call volume visible.

Consequences
Positive:
- Tool/model transient errors can still be retried without needing a new user
  message.
- A result re-entry cannot recursively create another detached Execution.
- Retry and cost bounds are deterministic, auditable, and independent of
  model compliance with the prompt.

Negative / tradeoffs:
- A task that remains unavailable after the retry budget ends requires the
  person to ask again rather than continuing indefinitely.
- Retry classification and backoff become explicit service behavior to test.
- A 90-second per-attempt timeout can make the worst-case background work
  longer; the attempt cap is therefore part of the product latency contract.

Rejected alternatives:
- Prompt Interaction to avoid redispatching: the incident demonstrates that
  prompt text is not a safety boundary.
- Never re-enter Interaction after a terminal run: loses the single-speaker
  persona and prevents a concise final response or clarification question.
- Ban all Execution retries: makes harmless transient Google/Composio
  failures user-visible and forces needless manual retries.
- Treat terminal rows as permanently deduped: blocks legitimate later user
  requests for the same goal.

Implementation status
- **Shipped (ADR 0019, task 049):** Interaction `dispatch_execution` is
  unavailable on internal result re-entry (`is_user_inbound=false`). This
  stops recursive redispatch.
- **Deferred (task 050):** in-run attempt counter, backoff, transient-retry
  classification, and related observability.

Amends
- ADR 0014: terminal result re-entry is retained, but it is no longer allowed
  to initiate another Execution. Recoverable retries happen inside the
  existing Execution lifecycle, not through Interaction.

Implementation
Task 050 (in-run retries). Redispatch guard: ADR 0019 / task 049.
