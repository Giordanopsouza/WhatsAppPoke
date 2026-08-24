---
id: 050-execution-in-run-retries
feature: agent-runtime
status: backlog
---

# Execution in-run retries (ADR 0018 remainder)

## Scope

Implement the in-run retry lifecycle deferred from task 049: one
`execution_run` owns bounded retries for transient provider/model failures
and per-attempt timeout, with persisted attempt count and exponential
backoff.

## Acceptance criteria

- [ ] Alembic migration + ORM field for non-null attempt counter per run.
- [ ] Settings: default max three attempts and bounded backoff; validated in
      `app/core/config.py`.
- [ ] `_run_execution` retries only classified transient failures on the same
      `execution_run.id`; intermediate failures do not finish the run, append
      terminal events, or re-enter Interaction.
- [ ] Logfire spans include attempt number and retry classification; document
      alert or query for abnormal call volume per run.
- [ ] Tests: transient retry then success; retries exhausted; non-retryable
      terminal failure; timeout retry; cancellation unchanged.
- [ ] Update `docs/glossary.md` and `docs/plan.md` as needed.

## Depends on

- Task 049.
- ADR 0018.
- ADR 0019.
