---
id: 022-composio-foundation
feature: integrations
status: done
---

# Composio foundation (schema, client, provider registry)

## Scope
First slice of the Composio-first epic: config, thin Python client, provider
registry, and DB shape so later tasks can connect accounts and attach MCP
without inventing schema mid-flight. **No** HTTP connect routes and **no**
agent/MCP wiring here.

Epic order: **022 → 023 → 024 → 025** (then Composio purge hooks in 021).

## Product decisions (locked in grooming)
- Composio-first with **managed auth** (MVP; no users yet — consent may show
  "Composio"; custom OAuth/white-label is post-MVP).
- DIY Google OAuth/tools are removed in **025**, not here.
- MVP registry (all managed-auth toolkits): `gmail`, `googlecalendar`,
  `googledrive`, `googlesheets`, `notion`, `trello`, `clickup`.
- Do **not** use `googlesuper` in MVP (managed auth can hit Google
  "App is blocked" on broad scopes).
- `user_id` for Composio sessions is always `str(contact_id)` — never
  `"default"`.
- Composio-only integrations: no local refresh-token path for new rows.

## Composio dashboard (manual, do first)
- [x] Composio project created; `COMPOSIO_API_KEY` in `.env` + `.env.example`
      + `app/config.py`
- [x] Project log storage set to **Don't store data** (LGPD — no tool
      payloads retained; see Composio data-retention docs)
      *(confirm in dashboard if not already — Settings → General → Log storage)*
- [x] Toolkits above enabled in the project (managed auth)

## Acceptance criteria
- [x] Dependency: official Composio Python v3 SDK in `pyproject.toml` via `uv`
      (no legacy `composio-pydanticai`)
- [x] `app/integrations/composio.py` (or equivalent): thin client —
      `create` session, `authorize(toolkit, callback_url=…)`,
      list/delete connected accounts (delete used later by 021)
- [x] Provider registry in code: allowlist + metadata (display name, PT
      copy for landing/notify) + Composio toolkit slug. Unknown provider
      rejected.
- [x] Migration reshapes `integration` for Composio-only:
  - `external_account_id` text nullable (Composio `ca_…` when active)
  - `refresh_token_enc` dropped **or** made nullable and unused
    (prefer drop if no prod rows to preserve)
  - `scopes` **dropped** (locked 2026-08-10: Composio manages scopes;
    no prod rows to preserve)
  - no `auth_backend=local` hedge unless a row must stay readable during
    025; default is Composio-only
  - unique `(contact_id, provider)` preserved; RLS + revoked Data API
    pattern per existing migrations
- [x] Migration extends `connect_link`: `provider` text NOT NULL; existing
      Google rows backfilled to `google` **or** `gmail` per registry
      choice documented in the migration (025 deletes DIY; prefer
      toolkit slugs going forward — if backfill uses `google`, 023/025
      must map or rewrite)
- [x] `docs/database.md` updated
- [x] `docs/deploy.md` updated: `COMPOSIO_API_KEY` on Railway (both api and
      worker services need it — 024 runs sessions from the worker)
- [x] Unit: registry rejects unknown providers; client module importable
      with settings

## Out of scope
- `/connect/{provider}` routes (023)
- Worker MCP / `MCPToolset` (024)
- Removing DIY Google code (025)
- Composio account purge on delete (021 — hook only documented here)
- Custom OAuth apps / white-label consent
- Enabling toolkits beyond the seven listed

## Depends on
- 007 (`integration` table)
- 008 (connect-link pattern — schema extended here, routes in 023)

## Log
### [PA] 2026-08-10 14:20 — Grooming
Split from monolithic `022-composio-connector` after architecture shift:
Composio-first (incl. Google), managed auth for MVP, four-task epic.
### [PA] 2026-08-10 14:30 — Architecture review
Locked: drop `scopes` column (Composio owns scopes; no prod rows). Added
`docs/deploy.md` env-var AC — worker also needs `COMPOSIO_API_KEY`.
### [PA] 2026-08-10 14:20 — Supersedes
`tasks/022-composio-connector.md` (hybrid Google-DIY draft) replaced by
022–025.
### [Agent] 2026-08-10 — Implementation
SDK + `COMPOSIO_API_KEY`, provider registry, thin client, migration
(`external_account_id`; drop `refresh_token_enc`/`scopes`;
`connect_link.provider` backfill `google`), docs + unit tests. DIY Google
refresh is a no-op until 025; Composio dashboard checklist still manual.
### [Agent] 2026-08-10 — Merged
Merged via PR #14; moved to `tasks/done/`.
