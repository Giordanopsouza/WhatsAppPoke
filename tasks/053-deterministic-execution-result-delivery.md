---
id: 053-deterministic-execution-result-delivery
feature: agent-runtime
status: done
---

# Deterministic execution-result delivery

## Migration preflight

Before implementation, inspect ADRs 0014, 0019, and 0022; tasks 049, 051,
and 052; the Interaction outbound reservation contract; and the
`execution_event` processed lifecycle.

Target end-state: every terminal Execution re-entry either produces one
idempotent visible outcome or remains explicitly unprocessed. A staged
sensitive write is described from its persisted `pending_action` and asks for
confirmation only after staging. `waiting_execution` is backed by a real
active Execution and is never accepted during terminal result re-entry.

No schema migration is expected. Do not add another agent, a long-lived agent
run, a second transport path, or a new queue. If implementation reveals that
the existing outbound/event identity cannot make a retried re-entry
idempotent, stop and record the required persistence change before coding a
temporary in-memory bridge.

## Scope

Close the Interaction liveness gap exposed by Logfire traces
`01a01cd1039b894969503175e4402b3c` and
`01a01cd12553c658ef5be1517a664c7e`: the original write request asked for
confirmation before staging, and the terminal re-entry then returned
`waiting_execution` without an outbound while its event was marked processed.

Make terminal result delivery deterministic where correctness requires it,
and enforce that internal Interaction state matches persisted runtime state.

## Acceptance criteria

### Terminal re-entry state

- [ ] `waiting_execution` is invalid when `event_kind == "execution_result"`.
      A model result with that state is not returned as a successful silent
      completion.
- [ ] A terminal re-entry cannot complete with zero visible outbound merely
      because the model selected `silent`, `done`, or `waiting_execution`.
      The runtime invokes the appropriate code-owned delivery path instead.
- [ ] `execution_event.processed_at` is set only after the re-entry reports a
      successful or deduplicated visible delivery. A no-outbound or failed
      delivery is logged and remains unprocessed; it is never acknowledged as
      successfully communicated.

### Persisted pending-action confirmation

- [ ] On terminal re-entry, select unexpired `pending_action` rows belonging
      to the current contact **and** the completed
      `source_execution_run_id`. Do not announce an unrelated older pending
      action merely because it belongs to the same contact.
- [ ] When the completed Execution staged one pending action, bypass model
      discretion and send one confirmation request through Interaction's
      existing `_send_visible` reservation/send path.
- [ ] Build the visible confirmation from the persisted action kind and
      payload, not from model prose. Calendar formatting includes the material
      event details; email formatting includes recipients, subject, and the
      draft information needed for informed approval.
- [ ] Never expose action IDs, execution IDs, provider IDs, payload hashes,
      tokens, or internal JSON in the visible confirmation.
- [ ] Multiple matching pending actions do not turn a bare “sim” into an
      ambiguous write. Present a safe disambiguation prompt or require the
      person to identify the intended action.
- [ ] The deterministic message only asks for confirmation. It never executes
      the sensitive write. The existing later-inbound confirmation gate and
      atomic claim remain unchanged.

### Code-owned terminal fallback

- [ ] When no matching pending action exists and the model produces no visible
      outbound, parse the terminal event and send a code-owned fallback based
      on `result.summary`, `result.outcome`, and terminal status.
- [ ] The fallback handles succeeded, failed, needs-input, timed-out,
      cancelled, and malformed/missing summaries without claiming an external
      side effect that is not known to have completed.
- [ ] Fallback text is compact, user-facing, and strips internal identifiers
      and raw exception details. Application logs and Sentry remain PII-free.

### Execution-backed waiting state

- [ ] `InteractionDeps` records the result of `dispatch_execution`, including
      whether a new or deduplicated active run backs the current Interaction.
- [ ] For `user_inbound`, accept `waiting_execution` only when dispatch started
      or found an active matching run, or when event context already contains
      an active Execution. `busy`, `unavailable`, terminal dedupe, and
      no-dispatch outcomes do not satisfy the invariant.
- [ ] An invalid inbound `waiting_execution` is surfaced in observability and
      converted to a non-waiting outcome; it cannot create a conversation that
      has no future wake-up source.

### Tool exposure and prompt sequencing

- [ ] Dynamically hide `confirm_event_create` and `confirm_email_send` on a
      user inbound when no compatible unexpired pending action exists. Expose
      only the confirmation tool kinds backed by persisted pending actions.
- [ ] Keep confirmation and dispatch tools absent from execution-result model
      schemas, with the current runtime guards retained as fail-closed safety.
- [ ] Update `interaction_prompt.md` explicitly: do not ask the person to
      confirm before a matching `pending_action` exists; for a new sensitive
      write request, first call `dispatch_execution`; any pre-dispatch progress
      message must not ask for confirmation.
- [ ] The documented conversational sequence is:
      request → Execution stages → Interaction asks from persisted data → a
      later user inbound confirms → Interaction executes the fixed action.

### Tests and observability

- [ ] Add a regression test for the exact incident: the original create-event
      request cannot leave `waiting_execution` without dispatch; staging
      creates a pending action; terminal re-entry sends the persisted
      confirmation; only the next inbound “sim” may create the event.
- [ ] Test an execution-result model response of `waiting_execution` with no
      tool calls and assert exactly one deterministic outbound plus a final
      non-waiting state.
- [ ] Test a model `done`/`silent` response with no outbound and no pending
      action, asserting the `result.summary` fallback.
- [ ] Test pending-action correlation by execution ID, multiple-action
      disambiguation, no action-ID leakage, disconnected integration, and
      malformed terminal payloads.
- [ ] Test that an execution event is not marked processed after no outbound or
      failed delivery, and is marked once after sent/deduplicated delivery.
- [ ] Test user-inbound `waiting_execution` with started, active-deduped,
      busy, unavailable, terminal-deduped, and no-dispatch cases.
- [ ] Logfire spans record event kind, requested versus effective state,
      invalid-state reason, dispatch outcome, delivery path
      (`model | pending_action | result_fallback`), outbound outcome, and
      processed decision without recording message bodies or credentials.
- [ ] Amend ADR 0019 or add a focused ADR documenting code-owned confirmation
      delivery and the execution-backed waiting invariant. Update task 051 to
      remove or close overlapping acceptance criteria in the same PR.
- [ ] Full test suite passes.

## Out of scope

- Changing the 15-minute pending-action expiry or its atomic claim semantics.
- Automatically confirming a write from the original request or from an
  internal execution-result event.
- Automatic reconciliation of an external write with unknown provider
  outcome.
- Whole-Execution retries and backoff (task 050).
- A general templating framework for all WhatsApp conversation text.
- New database tables, queues, transports, or agent definitions.

## Depends on

- Task 049 and ADR 0019: non-delegating result re-entry.
- Task 051: event-kind tool filtering and result-context groundwork already
  present in the codebase.
- Task 052 and ADR 0022: structured Execution outcome/status.
- Task 044: owned Calendar staging and later-turn confirmation boundary.

## Log

### [PA] 2026-08-19 21:51 — Grooming

Created from the production Calendar confirmation deadlock. In trace
`01a01cd1039b894969503175e4402b3c`, Interaction asked for confirmation and
returned `waiting_execution` without dispatching. After the person's early
“Sim”, trace `01a01cd12553c658ef5be1517a664c7e` staged the action successfully,
but terminal re-entry again returned `waiting_execution`, sent no outbound,
and allowed the execution event to be marked processed.

### [SWE] 2026-08-19 — Implemented

Terminal re-entry now sends a code-owned confirmation from pending actions
correlated to the completed execution, or a safe result fallback when no
model outbound exists. `execution_event` acknowledgement requires visible
sent/deduplicated delivery. Inbound `waiting_execution` is rejected without a
real active execution; confirmation schemas are exposed only for matching
pending kinds. ADR 0019 and task 051 were reconciled. Full suite: 186 passed.
