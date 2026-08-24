---
id: 044-owned-calendar-tools
feature: integrations
status: done
---

# Owned Google Calendar tools with create confirmation

## Migration preflight

Before implementation, inspect the relevant sections of `docs/plan.md`, the governing ADRs, this task, and its directly dependent or consuming tasks. Record:

- target end-state and contracts introduced here;
- legacy code allowed only as a temporary rollback bridge;
- legacy imports, data paths, and behaviors forbidden in new code;
- the task that removes each temporary bridge;
- an architecture test or CI check that enforces the boundary.

## Scope
Replace the Execution Agent's Calendar MCP surface with owned read tools
and a staged event-create flow over the authenticated proxy.

## Acceptance criteria
- [x] Execution registry exposes tools only for a connected Calendar:
      `list_calendars`, `list_events`, `get_event`, and
      `stage_create_event`.
- [x] Read tools use the contact timezone, bounded date ranges and result
      counts, and return normalized ids, titles, local times, attendees,
      locations, and status without raw provider payloads.
- [x] Relative dates are resolved from the injected contact clock before
      proxy execution; provider query timestamps are explicit.
- [x] `stage_create_event` validates calendar id, title, start/end,
      timezone, attendees, location, and description, then stores the
      exact normalized payload in `pending_action`. It does not create.
- [x] A later explicit inbound claims and executes one matching pending
      action after contact/hash/expiry validation. Duplicate confirmation
      cannot create a second event.
- [x] Same-turn instructions to “create and confirm” do not bypass the
      later-inbound rule.
- [x] No event update/delete, calendar permission mutation, generic proxy,
      or remote Composio schema is registered.
- [x] Unit/contract tests cover timezone boundaries, all-day vs timed
      events, disconnected Calendar, bounded lists, invalid ranges,
      multiple pending actions, duplicate confirmation, provider failure,
      and successful later create.
- [x] PII event descriptions/attendees and credentials stay out of logs.

## Out of scope
- Event update/delete/reschedule.
- Multiple Google accounts.
- Drive/Meet tooling.
- Natural-language recurrence creation for provider events.

## Depends on
- 039.
- 041.
- 042.
- ADR 0015.

## Log
### [PA] 2026-08-15 15:22 — Grooming
Calendar follows the Gmail owned-tool pattern: reads execute, writes stage,
and only a later WhatsApp inbound can authorize creation.

### [SWE] 2026-08-15 21:30 — Migration preflight and implementation
Target contract: Calendar-scoped Execution runs receive only four stable
owned business tools. Reads call fixed Calendar endpoints through the
contact's verified Composio account, resolve relative dates against the
injected contact clock, send explicit RFC3339 query bounds, and return
normalized ids/titles/local times/attendees/locations/status. Staging
persists the exact create payload, hash, expiry, and source
Interaction/Execution ids without inserting an event.

The legacy `composio_mcp.py`, Calendar remote schemas, tool allowlist,
and unflagged classifier/worker path remain only as the task 047 rollback
bridge. New Calendar code is forbidden from importing MCP, registering
remote action names, exposing endpoint/method/body arguments, or providing
update/delete/permission operations. Task 047 removes that bridge.
`tests/test_owned_calendar_tools.py`, `tests/test_composio_proxy.py`, and
`tests/test_execution_orchestration.py` enforce the registry, fixed-request,
timezone-boundary, staged-write, later-inbound, contact/hash/expiry,
ambiguity, duplicate, retry-release, and no-live-provider boundaries.

The Interaction Agent interprets natural-language confirmation from the
current user message. Its `confirm_event_create` orchestration tool is
available only on a real user inbound; an Execution-result re-entry is
refused. A same inbound therefore cannot stage and confirm its own create.
Different pending action kinds may coexist; a bare confirmation with more
than one create asks which action.

### [Tester] 2026-08-15 21:32 — Passed
`uv run pytest` passed: 238 tests. `git diff --check` also passed. No live
Composio, Google Calendar, Gemini, or Twilio call ran.
