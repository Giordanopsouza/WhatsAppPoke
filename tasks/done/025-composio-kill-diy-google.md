---
id: 025-composio-kill-diy-google
feature: integrations
status: done
---

# Remove DIY Google OAuth and local Gmail/Calendar tools

## Scope
Cut over completely to Composio (022–024): delete the local Google OAuth
client, Fernet-refresh Google integration path, and agent tools that call
`app/integrations/google.py`. No dual backend — there are no production
users to migrate.

## Acceptance criteria
- [x] Remove or stop registering local tools: `search_email`,
      `read_email`, `propose_send_email`, `list_events`, `create_event`,
      `delete_event`, and Google-specific `confirm_pending_action` kinds
      (`send_email`, `create_event`) — confirm tool remains only if other
      kinds still need it; otherwise slim or leave harmless
- [x] Remove the 024 gating shim (legacy-row check that kept DIY tools
      registered for old contacts) — after this task no local Google tool
      exists to gate
- [x] Remove `app/integrations/google.py`, `app/oauth_google.py`, and
      DIY routes (`/oauth/google/*`, hardcoded `/connect/google` if any
      remain after 023)
- [x] Drop unused settings from `app/config.py` + `.env.example`:
      `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` (keep
      `CONNECT_SIGNING_KEY`, `FERNET_KEY` if still used elsewhere —
      Fernet may remain for other secrets; if only Google used it,
      document whether to keep)
- [x] `pending_action` table may remain; no new Google kinds. Migration
      only if we want to prune check constraints — optional
- [x] System prompt / glossary: Google connect + mail/calendar go through
      Composio toolkits (`gmail`, `googlecalendar`, …)
- [x] ADR: short accepted ADR "Composio-first integrations" (managed auth
      MVP, deny-list for send/delete, supersedes hybrid draft) — update
      or amend ADR 0006 as needed
- [x] `docs/plan.md` Google-auth section rewritten for Composio connect +
      MCP tools
- [x] `docs/glossary.md`: `integration` / connect link no longer
      Google-only
- [x] Tests that asserted DIY Google OAuth/tool behaviour updated or
      removed; 023/024 tests remain green
- [x] Manual: no code path refreshes a Google token via Fernet; Gmail
      actions only via Composio MCP for a connected contact

## Out of scope
- Re-enabling Gmail send via propose-then-confirm (follow-up task)
- Composio connected-account purge (021)
- White-label / custom Google OAuth app on Composio

## Depends on
- 023 (Composio connect works for `gmail` / `googlecalendar`)
- 024 (MCP exposes Gmail/Calendar tools so DIY removal does not
  regress product capability)

## Log
### [PA] 2026-08-10 14:20 — Grooming
Fourth slice: delete DIY Google after Composio connect + MCP are live.
Clean cut — no parallel `auth_backend=local` hedge.
### [PA] 2026-08-10 14:30 — Architecture review
024 gates DIY tools on legacy rows instead of removing them; this task
now also deletes that gating shim.

### [PA] 2026-08-10 — Implementation
Removed DIY Google OAuth, local Gmail/Calendar tools, Fernet crypto,
and the 024 legacy gating shim. Composio connect + MCP is the only
integration path. ADR 0006/0008, plan, glossary, AGENTS updated.
