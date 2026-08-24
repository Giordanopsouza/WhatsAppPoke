---
id: 049-non-delegating-execution-result-reentry
feature: agent-runtime
status: done
---

# Non-delegating execution result re-entry

## Scope

Stop the execution-result redispatch loop by enforcing a runtime guard on
Interaction's `dispatch_execution` tool. Defer in-run Execution retries
(ADR 0018 remainder) to task 050.

## Acceptance criteria

- [x] `dispatch_execution` returns unavailable when `is_user_inbound` is
      false; it does not call the execution service.
- [x] User inbound (`app/api/dispatch.py`) still dispatches with
      `is_user_inbound=True`.
- [x] Worker automations continue to start runs via
      `run_scheduled_execution` without using the Interaction tool.
- [x] Tests cover denied internal re-entry and allowed user inbound
      dispatch.
- [x] ADR 0019 records the decision; ADR 0018 notes partial implementation.

## Out of scope

- Persisted attempt counter, backoff, and transient retries inside
  `_run_execution` (task 050).
- Logfire alert/query for abnormal model-call volume per run.

## Depends on

- ADR 0014.
- ADR 0018 (redispatch guard only).

## Log

### [PA] 2026-08-17 21:15 — Grooming

Logfire incident: 320 terminal Execution runs from result re-entry with
`dispatch_execution` still available.

### [PA] 2026-08-18 — Implemented (minimal)

Guard on `app/agent/interaction.py` `dispatch_execution`;
`tests/test_interaction_runtime.py`; ADR 0019. In-run retries deferred to
backlog task 050.
