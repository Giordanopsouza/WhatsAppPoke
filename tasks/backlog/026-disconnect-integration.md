---
id: 026-disconnect-integration
feature: integrations
status: pending
---

# Disconnect Composio integration (in-chat)

## Migration preflight

Before implementation, inspect the relevant sections of `docs/plan.md`, the governing ADRs, this task, and its directly dependent or consuming tasks. Record:

- target end-state and contracts introduced here;
- legacy code allowed only as a temporary rollback bridge;
- legacy imports, data paths, and behaviors forbidden in new code;
- the task that removes each temporary bridge;
- an architecture test or CI check that enforces the boundary.

## Scope
Let a contact disconnect a connected toolkit (e.g. Gmail) from WhatsApp:
revoke the Composio connected account and mark the local `integration` row
`revoked`, so MCP tools for that provider stop attaching on the next turn.

## Why now
A user tried to log out / disconnect their Gmail from Composio and could not.
Connect ships via `request_integration` (023); `delete_connected_account`
exists only for full account purge (021). There is no user-facing disconnect
path today (`manage_connections=False` on MCP sessions).

## Acceptance criteria
- [ ] Agent tool `disconnect_integration(provider)` (registry slug, same
      allowlist as `request_integration`): loads the contact's active row for
      that provider; if none → friendly "not connected" message; if present →
      call `delete_connected_account(external_account_id, revoke_on_delete=True)`
      then set local `integration.status=revoked` and clear
      `external_account_id` (or equivalent upsert). Idempotent if already
      revoked / already deleted at Composio.
- [ ] System prompt: when the person asks to disconnect / log out / unlink
      Gmail (or another connected app), use `disconnect_integration` — never
      invent a Composio dashboard URL or tell them to revoke only in Google.
- [ ] After disconnect, next agent turn must not attach that toolkit's MCP
      tools (`list_active_integrations` / `classify_integrations` already
      skip non-active; verify).
- [ ] Re-connect still works: `request_integration` → OAuth → new
      `external_account_id`, `status=active` (existing upsert path).
- [ ] Unit tests: happy path (Composio delete + local revoke); missing
      integration; unknown provider; Composio delete failure does not leave
      an inconsistent "active" row without a clear error (document chosen
      failure policy in the PR).
- [ ] Docs: note disconnect in `docs/plan.md` (Composio section) and
      `docs/glossary.md` if the term needs a one-liner.

## Out of scope
- Full account deletion / LGPD purge (021) — that still deletes *all*
  connected accounts as part of tenant wipe.
- Web UI / Composio dashboard self-serve disconnect.
- Propose-then-confirm two-step (MVP: one clear in-chat ask is enough;
  disconnect is reversible via reconnect).
- Revoking other Google grants the user may have made outside Composio.

## Depends on
- **022**–**025** (done): Composio client, connect flow, MCP attach,
  DIY Google removed. Reuses `app.integrations.composio.delete_connected_account`.

## Log
### [PA] 2026-08-11 21:40 — Grooming
User report: tried to disconnect Gmail from Composio and could not. Created
task for in-chat disconnect mirroring `request_integration`, using existing
`connected_accounts.delete` helper.
