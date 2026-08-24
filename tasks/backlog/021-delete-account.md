---
id: 021-delete-account
feature: privacy
status: pending
---

# Account deletion & LGPD right to erasure

## Migration preflight

Before implementation, inspect the relevant sections of `docs/plan.md`, the governing ADRs, this task, and its directly dependent or consuming tasks. Record:

- target end-state and contracts introduced here;
- legacy code allowed only as a temporary rollback bridge;
- legacy imports, data paths, and behaviors forbidden in new code;
- the task that removes each temporary bridge;
- an architecture test or CI check that enforces the boundary.

## Scope
Implement full tenant account deletion (`delete_account` / right to erasure under LGPD Art. 18) triggered via in-chat propose-then-confirm agent tool or support request. Purges or anonymizes all contact data across all tenant-scoped tables.

## Acceptance criteria
- [ ] DB query / cascade function: purge all tenant data linked to `contact_id` across `contact`, `message`, `integration`, `task`, `reminder`, `pending_action`, `usage_counter`, and harness long-term memory store.
- [ ] Agent write tools: `request_account_deletion` (proposes deletion & warns user) + `confirm_account_deletion` (executes cascade purge on confirmed turn).
- [ ] OAuth / connector revocation: before purging local rows, disconnect third parties — for Composio integrations call
      `connected_accounts.delete(external_account_id)` (client from task 022); legacy DIY Google revoke only if any
      local tokens still exist pre-025.
- [ ] Marco Civil compliance: preserve required 6-month access log metadata (IP, timestamp, message IDs) in an isolated, anonymized audit log table if required by law, stripping message bodies/PII.
- [ ] Unit tests: verify zero orphaned records remain in any tenant-scoped table after executing account deletion.
- [ ] Docs updated in `docs/plan.md` and `docs/database.md`.

## Out of scope
- Self-service web UI for account deletion (MVP handles deletion via in-chat WhatsApp confirmation).
- Automated time-based data retention purge cron (deferred to separate maintenance task).

## Depends on
- Prefer implementing after **022** (Composio client + `external_account_id`). Full Composio purge in prod paths
  needs connected accounts from **023**.

## Log
### [PA] 2026-08-09 17:30 — Grooming
Created task for LGPD compliance / account deletion flow following competitor privacy benchmark.
### [PA] 2026-08-10 14:20 — Composio epic
Revocation AC updated for Composio-first (022–025): delete connected accounts via Composio API, not only Google token revoke.
