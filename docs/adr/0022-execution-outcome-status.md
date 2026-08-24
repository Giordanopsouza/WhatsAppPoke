0022. Execution outcome status
Status: Accepted
Date: 2026-08-19

Context
ADR 0019 made execution result re-entry non-delegating, but a 2026-08-19
incident (Logfire trace `01a01bca0ee176bd5655a6a4ce257de8`) showed the next
failure mode: the Execution agent returned "não foi possível criar o
rascunho porque o assunto não pode ficar vazio; informe um assunto", yet
`execution_run.status` was `succeeded` because `_run_execution` mapped any
clean agent finish to `SUCCEEDED`. The re-entry Interaction saw a success,
sent a false "Enviando o e-mail agora…" message, and ended in
`waiting_execution` without dispatching — a deadlock that never asked the
person for the missing subject.

The signal that the goal failed lived only in the agent's free-text output,
which the Interaction agent did not reliably read. `execution_run.status` is
the structured field the re-entry context exposes; it must reflect outcome.

Decision
Make the Execution agent classify its own outcome, and map that to
`execution_run.status`. No new enum value, no migration.

- `agent_execution` returns an `ExecutionOutcome(status, summary)` where
  `status` is `succeeded` | `failed` | `needs_input`. `summary` is the short
  factual text the Interaction relays.
- `execution_prompt.md` tells the agent to use `succeeded` only when the
  goal was actually completed; `failed` when a tool rejected the work, an
  integration is missing, or data is insufficient and cannot be requested
  from the person; `needs_input` when advancing requires info from the
  person (e.g. email subject, date, recipient).
- `_run_execution` maps `succeeded` → `SUCCEEDED`, `failed` and
  `needs_input` → `FAILED`, and stores `result.outcome` so the re-entry
  context preserves the `needs_input` distinction without a new status.
- Interaction result-mode instruction (in `interaction_prompt.md`) reads
  `status` and `result.outcome`: relay `result.summary`, ask for the
  missing input when `outcome == "needs_input"`, never claim success or
  send a "sending now" status for a failed run, and end in `done` (never
  `waiting_execution`, since result re-entry cannot dispatch).

Consequences
Positive:
- The re-entry Interaction sees `status: "failed"` instead of a misleading
  `succeeded`, so it can ask for the missing input or report the failure.
- The classification is the agent's, where the semantic lives; the service
  layer stays a deterministic mapping.
- No schema/migration: reuses `FAILED` and carries the finer outcome in the
  result payload.

Negative / tradeoffs:
- Depends on the Execution model classifying honestly; a model that returns
  `succeeded` with a failure summary still misleads. Mitigated by prompt
  rules and reviewable in Logfire via `execution.outcome` span attribute.
- `needs_input` and other failures collapse to `FAILED` in the DB; the
  distinction is only in `execution_event.payload.result.outcome`, so
  dashboards that group by `execution_run.status` cannot separate them.
- Does not add in-run retries (ADR 0018 remainder, task 050) or the broader
  Interaction result-mode polish (task 051).

Rejected alternatives:
- Heuristic on the output text: fragile, false positives, locale-coupled.
- New `ExecutionRunStatus.NEEDS_INPUT` value: requires Alembic migration and
  a `ck_execution_run_status` update for a distinction the Interaction can
  already read from `result.outcome`.
- Propagate tool errors as exceptions: pydantic-ai surfaces them to the
  agent, not to the run; the agent still finishes clean, so status would
  still be `SUCCEEDED` without the structured output.

Amends
- ADR 0014: terminal Execution status now reflects agent-classified
  outcome, not merely "the run returned without raising".
- ADR 0019: the non-delegating re-entry now also receives an honest status.

Implementation
Task 052.
