---
id: 041-detached-execution-orchestration
feature: agent-runtime
status: done
---

# Detached Execution orchestration

## Migration preflight

Before implementation, inspect the relevant sections of `docs/plan.md`, the governing ADRs, this task, and its directly dependent or consuming tasks. Record:

- target end-state and contracts introduced here;
- legacy code allowed only as a temporary rollback bridge;
- legacy imports, data paths, and behaviors forbidden in new code;
- the task that removes each temporary bridge;
- an architecture test or CI check that enforces the boundary.

## Scope
Implement `dispatch_execution` as a deterministic lifecycle service and
wire one reusable Execution Agent. Runs are detached from Interaction,
persisted for audit, and return through a new Interaction event.

## Acceptance criteria
- [x] `dispatch_execution(goal, toolkit)` creates or returns a deduped
      `execution_run`; there is no Spawner Agent, named roster, or
      process-global cross-contact batch.
- [x] A contact may have at most two active executions. The third request
      returns a structured busy result that Interaction can explain.
- [x] Each accepted run starts an api `asyncio.Task`, transitions
      pending → running → terminal, and has a configurable 90-second
      default timeout.
- [x] One reusable `agent_execution` definition runs each goal in
      isolation. It never imports or calls Twilio and has no
      `send_message_to_user`.
- [x] Existing local business tools move from persona to the Execution
      registry: Tavily search, tasks, and deterministic reminder tools.
      `request_integration` remains on Interaction.
- [x] Execution receives only tools valid for the contact and requested
      toolkit/domain; disconnected SaaS schemas are absent.
- [x] Completion/failure/timeout/cancellation persists result + event,
      then schedules Interaction re-entry with the original goal,
      execution id, compact result, and latest visible history.
- [x] Result re-entry acquires the same per-contact Interaction lock as a
      user inbound. New user inbounds remain allowed while Execution
      runs.
- [x] Active execution summaries injected into Interaction contain only
      id, short goal, toolkit, status, and start time.
- [x] `cancel_execution` sets `cancel_requested`, cancels the local task
      when present, and reaches a terminal state without sending from
      Execution.
- [x] Startup/background cleanup marks expired `running` rows
      `abandoned`; it does not replay or notify automatically.
- [x] Tests cover max-two, dedupe, timeout, cancel race, stale result
      after a newer user message, latest-history re-entry, no direct
      Twilio import, and contact isolation.

## Out of scope
- Durable replay of lost conversation executions.
- Named/persistent Execution agents.
- Composio proxy or SaaS tools (042–044).
- Batch aggregation across unrelated execution runs.

## Depends on
- 039.
- 040.
- ADR 0014.

## Log
### [PA] 2026-08-15 15:22 — Grooming
“Spawn” is lifecycle, not intelligence. Persist state and re-enter the
single speaker, but intentionally do not turn execution rows into jobs.

### [SWE] 2026-08-15 17:10 — Migration preflight and implementation
Target contract: `dispatch_execution` persists one contact-scoped,
deduped `execution_run`, starts an in-process task with a 90-second default
timeout, records a terminal `execution_event`, and re-enters the locked
Interaction runtime with current visible history. `agent_execution` is the
one reusable, non-speaking definition; its dynamic registry exposes only the
requested local domain in this slice. Connected Gmail/Calendar intentionally
receive no schemas until 042–044 add owned tools.

Temporary rollback bridge: the legacy persona still imports its existing
local tools and MCP execution path for unflagged contacts. New Interaction
and Execution code neither import that legacy loop nor attach MCP. Task 047
removes the bridge, classifier/ack path, and legacy tool imports. New code is
forbidden from importing Twilio or calling `send_message_to_user` from
Execution, from attaching disconnected SaaS schemas, or from introducing a
roster/global batch manager. `tests/test_execution_orchestration.py` enforces
the Execution transport boundary, per-toolkit schema absence, lifecycle
dedupe/busy/timeout/cancel behavior, re-entry contract, and no-notify stale
cleanup.

### [Tester] 2026-08-15 17:11 — Passed regression suite
Focused lifecycle contracts and the full test suite passed locally (191
tests). No live Gemini, Twilio, or provider calls ran in CI.
