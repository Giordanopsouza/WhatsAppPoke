---
id: 040-interaction-agent-runtime
feature: agent-runtime
status: done
---

# Single Interaction Agent runtime

## Migration preflight

Before implementation, inspect the relevant sections of `docs/plan.md`, the governing ADRs, this task, and its directly dependent or consuming tasks. Record:

- target end-state and contracts introduced here;
- legacy code allowed only as a temporary rollback bridge;
- legacy imports, data paths, and behaviors forbidden in new code;
- the task that removes each temporary bridge;
- an architecture test or CI check that enforces the boundary.

## Scope
Build the new contact-scoped Interaction runtime behind a disabled
feature flag. It becomes the only WhatsApp speaker and supports
idempotent multi-message output; the legacy classifier/ack path remains
available until task 047.

## Acceptance criteria
- [x] One `agent_interaction` Pydantic Agent uses the existing WhatsApp
      persona/model and a typed internal output:
      `done | waiting_execution | silent`.
- [x] The agent has orchestration tools only:
      `send_message_to_user`, `request_integration`, `wait`, and a
      temporary `dispatch_execution` stub that reports unavailable until
      041 wires lifecycle.
- [x] `send_message_to_user` reserves `(interaction_run_id, sequence)`
      before Twilio, sends once, and persists provider id/delivery state.
      Repeating the same tool call returns the existing result.
- [x] A hard runtime fuse rejects the sixth visible outbound in one
      Interaction run. There is no prompt-only enforcement.
- [x] No caller sends `result.output`; all visible text goes through
      `send_message_to_user`.
- [x] One Interaction event loads recent visible messages, contact clock,
      connected-toolkit summary, pending actions, and active execution
      summaries without exposing internal event payloads as user messages.
- [x] Interaction events acquire a Postgres advisory lock per contact
      for one run. The Twilio webhook itself does not wait on that lock or
      on the model.
- [x] A transient model failure retries once only if no visible outbound
      was reserved. Otherwise it does not replay the run.
- [x] Two pre-send failures reserve/send the fixed fallback once.
- [x] New runtime is callable after webhook 200 under a contact-scoped
      feature flag that defaults off. Existing production path is
      unchanged for non-flagged contacts.
- [x] Unit tests cover typed output, five-message fuse, duplicate tool
      call, fallback, retry boundary, cross-contact lock key, and webhook
      200 not awaiting Interaction. No live Gemini/Twilio in CI.

## Out of scope
- Detached Execution implementation (041).
- Removing classifier, ack, or `agent_turn` (047).
- Gmail/Calendar owned tools.
- Working memory.

## Depends on
- 039.
- ADR 0014.

## Log
### [PA] 2026-08-15 15:22 — Grooming
One speaker contract: messages leave only through the tool; the Pydantic
output controls runtime state and is never sent to WhatsApp.

### [SWE] 2026-08-15 16:15 — Implemented disabled Interaction runtime
Added the contact-scoped feature flag, typed Interaction agent, Postgres
advisory lock, idempotent outbound delivery path, model retry boundary, and
temporary detached-execution stub. Legacy dispatch remains the default.

### [Tester] 2026-08-15 16:16 — Passed local regression suite
Focused Interaction tests and the full pytest suite passed with no live
Gemini or Twilio calls. This task remains in progress until its isolated
changes can be committed from a clean task branch.

### [SWE] 2026-08-15 16:25 — Removed legacy runtime coupling
Interaction now has its own prompt and imports only runtime-neutral model,
visible-history, and managed-auth-link helpers. It no longer imports the
legacy loop, legacy prompt, or legacy tool registry.

### [Tester] 2026-08-15 16:26 — Regression passed after boundary cleanup
`uv run pytest -q` passed all 184 tests. The new runtime and legacy fallback
path remain covered without live provider calls.

### [SWE] 2026-08-15 16:30 — Added migration preflight to task tracker
Every executable plan and backlog task now requires its agent to inspect the
plan, ADRs, and directly related tasks before implementation, then record the
target boundary, temporary bridges, forbidden legacy dependencies, removal
task, and an enforcing architecture check.

### [SWE] 2026-08-15 16:35 — Kept concrete tool scope out of Interaction
Renamed Interaction's conversation-only connected-app context from legacy
`connected_toolkits` to `connected_integrations`. Future Execution owns the
concrete tool scope and allowlist.

### [Tester] 2026-08-15 16:40 — Completed and committed
The full regression suite passed (184 tests) and the migration change set was
committed on its dedicated branch. Task 040 is ready for review.
