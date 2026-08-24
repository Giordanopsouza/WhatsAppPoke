---
id: 001-job-queue-table
feature: runtime
status: done
---

# Job queue table

## Scope
Alembic migration adding the `job` table — the durable queue the worker
claims from. Delayed execution and retry fields included so reminders and
backoff need no extra infrastructure.

## Acceptance criteria
- [x] `job` table: `id` uuid pk (`gen_random_uuid()`), `contact_id` FK to
      `contact` following the existing explicit `ON DELETE` convention,
      `kind` text check in (`agent_turn`, `reminder_due`,
      `integration_notify`), `payload` jsonb not null default `{}`,
      `run_at` timestamptz not null, `status` text default `pending`
      check in (`pending`, `running`, `done`, `dead`), `attempts` int
      default 0, `max_attempts` int default 5, `locked_at` timestamptz
      null, `created_at` default now()
- [x] Partial unique index: one pending `agent_turn` per contact
      (`WHERE status = 'pending' AND kind = 'agent_turn'`)
- [x] Index on `(status, run_at)` for the claim query
- [x] RLS enabled + Data API grants revoked, mirroring migration
      `28b0ac108edc`
- [x] `uv run alembic upgrade head` clean against Supabase

## Out of scope
- Worker process, claim/execute logic (task 002)
- pgmq or any extension-based queue

## Log
### [PA] 2026-08-05 15:45 — Grooming
Created from `docs/plan.md` Phase A.
### [SWE] 2026-08-06 11:15 — Start
Implementing Alembic migration `183da715dd33_create_job_table`.
### [SWE] 2026-08-06 11:20 — Complete
Migration applied (`28b0ac108edc` → `183da715dd33`). Verified on Supabase:
table shape, `ON DELETE RESTRICT`, partial unique + claim indexes, RLS on,
anon/authenticated grants empty.
