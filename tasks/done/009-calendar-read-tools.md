---
id: 009-calendar-read-tools
feature: integrations
status: done
---

# Google client + Calendar read tools

## Scope
The shared Google API client module (token refresh, revocation
handling) plus the first Calendar tools. Read-only: agenda and event
listing, timezone-aware via `contact.tz`.

## Acceptance criteria
- [x] `app/integrations/google.py`: exchange stored refresh token →
      access token, serialized per contact with a pg advisory lock
      (concurrent refreshes can invalidate each other)
- [x] `invalid_grant` on refresh → integration marked `revoked`; agent
      tells the user to reconnect via `request_integration` instead of
      erroring
- [x] All Google calls async (`httpx.AsyncClient`), hard timeout ≤ 8s
- [x] Migration adds `contact.tz` text, default `America/Sao_Paulo`
- [x] Tools: `today_agenda` and `list_events(days_ahead)` — returned
      times rendered in the contact's tz, concise list format, no raw
      ISO dumps in chat
- [x] Fixture-based tests for the client module (mock Google responses:
      success, refresh, invalid_grant)

## Out of scope
- `create_event` (task 011)
- Recurring-event expansion edge cases beyond Google API defaults

## Log
### [PA] 2026-08-05 15:45 — Grooming
Created from `docs/plan.md` Phase C. Depends on 008, 004.

### [SWE] 2026-08-07 20:05 — Start
Implementing Google client + Calendar read tools on branch
`009-calendar-read-tools`.

### [SWE] 2026-08-07 20:20 — Done
Shipped `app/integrations/google.py` (refresh under
`GOOGLE_TOKEN_LOCK_NS` advisory lock, `invalid_grant` → revoked),
migration `b2e9f1a04c83` for `contact.tz`, tools `today_agenda` /
`list_events`, fixture tests. `alembic upgrade head` applied;
`pytest` 28 passed.
