---
id: 051-context-bound-execution-result-reentry
feature: agent-runtime
status: backlog
---

# Context-bound Execution result re-entry

## Migration preflight

Target: keep Interaction runs short and select their tool surface from the
event that started them. Do not create another agent, keep an `agent.run()`
alive while waiting, or change task 050's whole-Execution retry lifecycle.

## Scope

Make Execution result re-entry a non-delegating Interaction mode that always
has a path to communicate success, pending confirmation, missing input, or
failure without calling tools that require a new user message.

## Acceptance criteria

- [ ] Replace the ambiguous boolean-only context with an explicit Interaction
      event kind covering at least `user_inbound` and `execution_result`.
- [ ] Pass `execution_result` from `_reenter_interaction`; genuine Twilio
      messages continue to pass `user_inbound` and retain their provider
      message id as `inbound_turn_id`.
- [ ] Use Pydantic AI tool preparation/filtering so `dispatch_execution`,
      `confirm_email_send`, and `confirm_event_create` are absent from the
      model schema during `execution_result` re-entry.
- [ ] Keep the runtime guards on dispatch and confirmation. A filtering
      regression must fail closed without creating an Execution or confirming
      a pending action.
- [ ] Execution-result mode may use only the minimal conversational tools:
      `send_message_to_user`, `wait`, and `request_integration` when a failed
      result requires reconnection.
- [ ] Inject a short dynamic instruction for result mode: communicate the
      terminal result; when a `pending_action` exists, show its exact summary
      and ask for confirmation in a later message.
- [ ] A failed Execution produces one terminal re-entry. Interaction reports
      the failure or asks for missing input; it never retries the work itself.
      A user reply such as “tenta de novo” is a new inbound and may dispatch a
      new Execution normally.
- [ ] A write with an unknown external outcome is reported as unresolved and
      is not automatically repeated from result re-entry. Detailed in-run
      retry counters/backoff remain task 050.
- [ ] `UsageLimitExceeded` does not trigger a second identical Interaction
      attempt; use the existing idempotent fallback once when no outbound was
      reserved.
- [ ] Tests inspect the actual model tool definitions for both event kinds and
      cover: staged Calendar confirmation prompt; failed Execution; missing
      integration link; forbidden dispatch/confirmation side effects; and no
      second attempt after `UsageLimitExceeded`.
- [ ] Logfire records the event kind, terminal Execution status, exposed tool
      names, model request count, and outbound outcome without adding message
      bodies or credentials to application logs.

## Out of scope

- Persisted attempt counters, whole-Execution retries, and backoff (task 050).
- A long-lived Interaction coroutine or per-contact actor/mailbox.
- Automatic reconciliation of ambiguous Gmail or Calendar writes.
- Changes to pending-action expiry or confirmation semantics.
- New database tables, queues, or agent definitions.

## Depends on

- Task 049.
- ADR 0019.
- ADR 0021.

## Log

### [PA] 2026-08-19 — Grooming

Created from the Calendar staging incident. Runtime guards prevented external
effects, but forbidden tools remained visible and consumed both Interaction
request budgets before fallback.

### [PA] 2026-08-19 21:51 — Deterministic liveness follow-up

Task 053 owns the production follow-up for code-owned pending-action
confirmation delivery, terminal-result fallback, execution-backed
`waiting_execution`, and processed-event acknowledgement. When task 053 is
implemented, reconcile this task's overlapping acceptance criteria rather than
shipping two independent result-reentry designs.

### [SWE] 2026-08-19 — Reconciled

Task 053 implemented the overlapping terminal delivery, pending-action
correlation, waiting-state invariant, and processed-event acknowledgement.
This task remains for future tool-schema/context work not already covered by
those runtime guards.
