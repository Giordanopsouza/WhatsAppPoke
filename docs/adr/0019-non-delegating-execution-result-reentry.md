0019. Non-delegating execution result re-entry
Status: Accepted
Date: 2026-08-18

Context
ADR 0014 routes every terminal Execution result through Interaction so the
sole WhatsApp speaker can interpret and communicate it. ADR 0018 described a
full bounded retry-chain contract: in-run retries plus blocking
`dispatch_execution` on internal result re-entry.

On 2026-08-17, a production incident showed the urgent failure mode: terminal
Execution → `execution.result_reentry` → Interaction still exposed
`dispatch_execution`; the model redispatched the same work. One trace created
320 sequential runs and 3,775 Gemini calls.

Prompt text is not a safety boundary for this control flow. The same pattern
already exists for staged confirmations: `confirm_email_send` and
`confirm_event_create` return unavailable unless `is_user_inbound` is true.

Scheduled automations do not use the Interaction tool. The worker calls
`run_scheduled_execution` directly and only re-enters Interaction after the
run finishes (or when a required toolkit is missing).

Decision
Ship the redispatch guard now; defer in-run Execution retries to a later task.

- `dispatch_execution` on Interaction checks `InteractionDeps.is_user_inbound`.
  When false (execution result re-entry, automation skip/summary, or any other
  internal event), the tool returns a structured unavailable result and does
  not call `app.services.execution.dispatch_execution`.
- A genuine user inbound continues to set `is_user_inbound=True` in
  `app/api/dispatch.py` and may dispatch normally.
- Worker automations keep starting Execution through
  `run_scheduled_execution`; this ADR does not change that path.
- Internal result re-entry may still `send_message_to_user`, `wait`, or
  `cancel_execution`; it may not start a successor detached run.

Consequences
Positive:
- Stops the recursive redispatch loop that caused the 2026-08-17 incident.
- One small runtime guard, tested, independent of model compliance.
- Automations and conversation dispatch remain on their existing entry paths.

Negative / tradeoffs:
- A result re-entry cannot chain into a second detached Execution even when
  that might seem convenient; the person must send a new inbound message.
- Transient provider failures still surface as one terminal run until
  in-run retries from ADR 0018 are implemented separately.

Rejected alternatives:
- Prompt-only “do not redispatch”: rejected in ADR 0018; insufficient.
- Block re-entry entirely: loses the single-speaker final response.
- Gate only on `execution.*` event kinds in the service layer: duplicates
  knowledge already carried by `is_user_inbound` on Interaction deps.

Amends
- ADR 0014: terminal result re-entry is retained but is non-delegating for
  new Execution dispatches.
- ADR 0018: the in-run attempt counter, backoff, and transient-retry
  lifecycle remain accepted intent but are not part of this change.

Further amendment (2026-08-19)

Terminal re-entry has a code-owned liveness contract. When the completed
Execution staged pending actions, Interaction selects only rows correlated to
that execution and sends the persisted confirmation request itself. Otherwise
a silent/done/waiting model output receives a safe terminal-result fallback.
The event is acknowledged only after a sent or deduplicated visible delivery.
`waiting_execution` is accepted for a user inbound only when dispatch started
or found an active run (or the event already has active-run context).

Implementation
Task 049. In-run retries: backlog task 050.
