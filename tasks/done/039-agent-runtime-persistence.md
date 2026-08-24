---
id: 039-agent-runtime-persistence
feature: agent-runtime
status: done
---

# Agent runtime persistence primitives

## Migration preflight

Before implementation, inspect the relevant sections of `docs/plan.md`, the governing ADRs, this task, and its directly dependent or consuming tasks. Record:

- target end-state and contracts introduced here;
- legacy code allowed only as a temporary rollback bridge;
- legacy imports, data paths, and behaviors forbidden in new code;
- the task that removes each temporary bridge;
- an architecture test or CI check that enforces the boundary.

## Scope
Add the Postgres/Alembic foundation for detached executions, internal
events, and idempotent multi-message outbound. No agent or webhook
behavior changes in this task.

## Acceptance criteria
- [x] Alembic migration + ORM model for `execution_run`, tenant-scoped
      by `contact_id`, with goal, toolkit scope, status, dedupe key,
      compact result/error, cancellation flag, and lifecycle timestamps.
- [x] Status constraint covers `pending`, `running`, `succeeded`,
      `failed`, `timed_out`, `cancel_requested`, `cancelled`, and
      `abandoned`.
- [x] Partial unique index prevents two active runs with the same
      `(contact_id, dedupe_key)`; a separate active-contact index supports
      the service-level maximum of two concurrent runs.
- [x] Alembic migration + ORM model for `execution_event` with
      `contact_id`, `execution_run_id`, kind, JSON payload, `created_at`,
      and nullable `processed_at`. Internal events are not `message` rows.
- [x] `message` can reserve an outbound before Twilio with nullable
      `interaction_run_id`, `outbound_sequence`, and delivery state.
      Partial unique index on
      `(contact_id, interaction_run_id, outbound_sequence)` applies only
      to Interaction outbounds.
- [x] Evolve `pending_action` for owned writes: payload hash,
      source Interaction/execution id, and terminal statuses needed to
      distinguish executed, cancelled, expired, and failed from
      pending/claimed. Existing rows migrate safely.
- [x] DB helpers atomically create/claim/finish runs, append/mark events,
      count active runs, request cancellation, mark stale runs abandoned,
      and reserve/update one outbound sequence.
- [x] Every new tenant table follows existing FK, RLS, revoked Data API,
      naming, and timestamp conventions. Migration downgrade is complete.
- [x] Tests cover cross-contact isolation, active dedupe, outbound
      idempotency, execution/pending-action transitions, and
      stale-to-abandoned behavior.
- [x] `docs/database.md` ER diagram and quick reference match the
      migration.

## Out of scope
- Pydantic agents or prompts.
- Starting asyncio tasks.
- Twilio sends.
- Automation/RRULE schema (045).

## Depends on
- ADR 0014.

## Log
### [PA] 2026-08-15 15:22 — Grooming
First migration slice for the Interaction/Execution runtime. These rows
provide audit and idempotency, not a replacement conversation queue.
