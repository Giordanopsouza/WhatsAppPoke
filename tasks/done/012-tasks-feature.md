---
id: 012-tasks-feature
feature: features
status: done
---

# Tasks feature

## Scope
Per-contact todo list: `task` table plus agent tools to add, list, and
complete tasks. Pure Postgres, no third-party integration — the first
feature that is entirely ours.

## Acceptance criteria
- [x] Migration: `task` (`contact_id` FK, `title`, `status` check in
      (`open`, `done`), `due_at` timestamptz null, timestamps); RLS
      pattern per `28b0ac108edc`
- [x] Tools: `add_task(title, due_at?)`, `list_tasks`,
      `complete_task(index)`
- [x] `complete_task` takes 1-based `list_tasks` index; agent lists /
      asks which number when the person is vague
- [x] `list_tasks` output concise, open tasks first, due dates in the
      contact's tz
- [x] Fixture-based tests for the tools against the real schema

## Out of scope
- Recurring tasks, priorities, projects/tags
- Task reminders (users compose `set_reminder` instead)

## Log
### [PA] 2026-08-05 15:45 — Grooming
Created from `docs/plan.md` Phase D. Depends on 004.

### [A] 2026-08-09 21:30 — Implementation
Migration + ORM + `add_task` / `list_tasks` / `complete_task` (index
only; agent resolves ambiguity). Tests in `tests/test_tasks.py`.

### [A] 2026-08-09 21:40 — Simplify
Dropped fuzzy `match.py`; `complete_task(index)` only.
