---
id: 052-execution-outcome-status
feature: agent-runtime
status: done
---

# Execution outcome status

## Scope

Make `execution_run.status` reflect whether the Execution agent actually
completed the goal. Today any clean agent finish is `succeeded`, even when the
agent's own text says it could not (e.g. Gmail rejecting an empty subject).
Give the Execution agent a structured outcome (`succeeded` / `failed` /
`needs_input`) and map it to `ExecutionRunStatus` so the re-entry Interaction
sees the real terminal state.

## Acceptance criteria

- [ ] `agent_execution` returns an `ExecutionOutcome(status, summary)` instead
      of `str`; `run_execution_goal` returns that object.
- [ ] `execution_prompt.md` instructs the agent to classify the outcome:
      `succeeded` only when the goal was completed; `failed` when a tool
      rejected the work or data is missing/unavailable; `needs_input` when it
      needs info from the person to proceed.
- [ ] `_run_execution` maps `succeeded` → `SUCCEEDED`, `failed` and
      `needs_input` → `FAILED` (no new enum value, no migration), and stores
      `result.outcome` so the re-entry context can distinguish them.
- [ ] Interaction result-mode instruction handles `failed`/`needs_input`:
      relay `result.summary` to the person, ask for missing input when
      `outcome == "needs_input"`, never claim success, never send a
      "sending now" status, end in `done`.
- [ ] Tests updated: the `run_execution_goal` mock returns an
      `ExecutionOutcome`; a `failed` outcome finishes the run as `FAILED`.

## Out of scope

- New `ExecutionRunStatus` enum value or Alembic migration (reuse `FAILED`).
- Persisted attempt counters / in-run retries (task 050).
- Interaction-side tool filtering and event-kind work already covered by
  task 051 (mostly present in code today).

## Depends on

- Task 049 (redispatch guard).
- ADR 0019.

## Log

### [PA] 2026-08-19 17:15 — Grooming

Root cause from Logfire trace `01a01bca0ee176bd5655a6a4ce257de8`: the
Execution agent returned "não foi possível criar o rascunho … informe um
assunto" but `execution_run.status` was `succeeded`, so the re-entry
Interaction saw a success and sent a false "Enviando o e-mail agora…" plus a
deadlocked `waiting_execution`. Option 1 from the trace review.

### [SWE] 2026-08-19 17:20 — Implemented

`ExecutionOutcome(status, summary)` on `agent_execution`; prompt rules for
`succeeded`/`failed`/`needs_input`; `_run_execution` maps to `SUCCEEDED`/
`FAILED` and stores `result.outcome`; Interaction result-mode instruction
handles `failed`/`needs_input`. ADR 0022; glossary entry. Tests updated and
extended (`test_failed_outcome_finishes_run_as_failed`). 180 passing.
