---
id: 047-agent-runtime-canary-cutover
feature: agent-runtime
status: in-progress
---

# Make Interaction/Execution the only runtime

## Migration preflight

Inspected `docs/plan.md` (target runtime and release gates),
ADRs 0014–0016, this task, task 015, and consuming tasks 040–046.

- **Target end-state:** every ordinary inbound starts Interaction after the
  webhook 200; Interaction is the only visible speaker; detached Execution
  returns through an internal event; worker retains durable background jobs.
  There is one inbound route, with no contact selector or legacy fallback.
- **Temporary rollback bridge:** none. Task 047 removes the old runtime in
  this release; the cleanup migration discards its obsolete queued jobs.
- **Forbidden in new runtime code:** classifier selection, hardcoded ack,
  `agent_turn` enqueue, `ask_execution`, MCP sessions, remote schemas, and
  allowlist/deny-list policy. Owned Gmail/Calendar tools use only the fixed
  authenticated proxy, and unowned connected providers are not attached to
  Execution.
- **Bridge removal:** this task's Alembic migration deletes obsolete
  `agent_turn` rows and removes the job kind/index; the same change deletes
  the rollback modules, handler, MCP policy, and obsolete tests.
- **Architecture/CI boundary:** `tests/test_runtime_cleanup.py` asserts that
  the inbound dispatcher uses only Interaction, the background-only job kinds
  remain, and all legacy agent/MCP modules are absent.

## Scope
Use Interaction as the sole inbound conversation runtime and remove all legacy
conversation modules, configuration, job kind, and tests. Background jobs and
worker remain.

## Acceptance criteria
- [x] Every non-empty inbound reaches Interaction; no selector or second
      conversation route exists.
- [x] Logfire spans/metrics distinguish webhook, Interaction,
      first-visible outbound, Execution, proxy tool, result re-entry,
      timeout, abandonment, duplicate suppression, and pending-action
      confirmation. No PII bodies/tokens.
- [ ] Proposed gates are measured on representative traffic:
      chat first-visible p95 <3s, optional first status p95 <4s,
      Gmail/Calendar result p95 <30s, webhook p95 <2s, zero duplicate
      run/sequence sends, and zero unconfirmed sensitive writes.
- [x] The cleanup migration discards obsolete `agent_turn` rows and retires
      their job kind/index before deployment removes the handler.
- [x] Remove classifier, `agent_chat`, hardcoded ack, nested synchronous
      `ask_execution`, MCP attach/session policy, allowlist/deny-list, and
      obsolete Gmail MCP pins/tests.
- [x] Keep job queue/worker handlers for reminders, automations,
      integration notifications, outbound sweep/due, retries, and dead
      letters.
- [x] Existing active Gmail/Calendar connections continue without
      reconnect. Connected providers without owned tools are described
      honestly and are not attached to Execution.
- [x] `AGENTS.md`, `docs/deploy.md`, `docs/glossary.md`,
      `docs/database.md`, `.env.example`, task references, and stale
      runtime tests match the shipped architecture.
- [ ] Offline task 015 evals and full pytest suite pass; manual WhatsApp
      cases cover chat, five bubbles fuse, Gmail read/draft/confirm,
      Calendar read/create/confirm, reminder, automation, briefing,
      overlapping user message, timeout, and fallback.

## Out of scope
- Removing the worker service or Postgres job table.
- Working memory (014).
- Owned tools beyond Gmail/Calendar.
- Replaying abandoned conversation executions.

## Depends on
- 040–046.
- 015 release-gate cases.
- ADRs 0014–0016.

## Log
### [PA] 2026-08-15 15:22 — Grooming
Cutover is the only task allowed to delete the old runtime.

### [SWE] 2026-08-16 01:30 — Interaction-only cleanup
Removed the canary/legacy selector and made Interaction the sole inbound
conversation path. The Alembic migration discards obsolete `agent_turn` rows,
retires the job kind/index, and the application no longer contains the
classifier, ack, legacy persona/exec, MCP session policy, or conversation
worker handler. Durable background handlers, owned tools, PII-safe telemetry,
and existing integration connections remain.
