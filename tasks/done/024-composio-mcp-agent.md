---
id: 024-composio-mcp-agent
feature: integrations
status: done
---

# Composio MCP on the worker agent

## Scope
Per agent turn, attach a Composio MCP toolset for the contact's active
integrations so the model can read/draft/write through Composio. Enforce
a deny-list for sensitive irreversible actions (email send/reply and
deletes). Local tools (tasks, reminders, Tavily, `request_integration`)
stay.

## Product decisions (locked)
- Pydantic AI MCP client: `MCPToolset` via `pydantic-ai-slim[mcp]` (not
  legacy `composio-pydanticai`; prefer `session.mcp.url` +
  `session.mcp.headers` with `mcp=True`).
- Session: `composio.create(user_id=str(contact_id), toolkits=[…],
  mcp=True)` — toolkits from that contact's **active** composio
  integrations (not a shared global user).
- `manage_connections=False` (or equivalent) so the agent cannot mint
  raw Composio connect URLs; connect remains 023 /
  `request_integration`.
- Prefer `SESSION_PRESET_DIRECT_TOOLS` (or explicit tool filters) so
  meta-tool search does not dump hundreds of schemas.
- **Allow:** reads, drafts, creates/updates (events, cards, pages, sheet
  cells, etc.).
- **Deny:** Gmail send/reply (`GMAIL_SEND_*`, `GMAIL_REPLY_*`); any
  destructive delete / batch-delete / permanent trash matching a clear
  slug/tag policy (document the exact matcher in code + this task's
  PR).
- MCP bypasses SDK `before_execute` modifiers — policy must be
  **tool allow/deny at session creation**, not post-hoc hooks.
- **Tool-surface gate (locked 2026-08-10):** while DIY Google code still
  exists (until 025), register the local Google tools
  (`search_email`/`read_email`/`list_events`/`create_event`/
  `delete_event`/`propose_send_email`) **only when the contact has a
  legacy local integration row**; contacts connected via Composio get
  MCP tools instead — never both at once.
- **MCP failure policy (locked 2026-08-10):** if session creation or
  toolset attach fails/times out, degrade gracefully — run the turn with
  local tools only and log a warning; do **not** fail the job into
  retry/dead-letter because Composio is down.
- **Telemetry (accepted risk 2026-08-10):** MCP tool args/results (email
  bodies, page content) will appear in Logfire spans; accepted for MVP
  (controlled access, no users yet). Revisit with redaction or
  `instrument=False` before real users — note in `docs/plan.md`
  deferred work.

## Acceptance criteria
- [x] `uv add` MCP extra: `pydantic-ai-slim[google,logfire,mcp]` (or
      equivalent) so `MCPToolset` imports
- [x] Worker turn: if contact has ≥1 active composio integration, create
      session + attach MCP toolset alongside local tools; if none, skip
      MCP (local tools only)
- [x] Deny-list enforced on the session tool filter; unit test proves
      send/delete slugs are not exposed for a gmail-enabled session
- [x] Tenancy: contact A session never uses contact B's `user_id`
- [x] Gate: local DIY Google tools registered only for contacts with a
      legacy local integration row; Composio-connected contacts never see
      both surfaces (unit: gmail-via-Composio contact does not get
      `search_email` in the tool list)
- [x] Failure: simulated Composio session-creation error → turn still
      completes with local tools; warning logged with `contact_id`
      (no PII)
- [x] System prompt updated: which providers exist, how to ask user to
      connect, that email **send** is unavailable (draft/read OK) until
      a future confirmation task
- [ ] Manual: connected Notion → agent lists/searches content for that
      contact only
- [ ] Manual: connected Gmail → agent can fetch/read (and draft if
      exercised); attempting send is impossible or clearly refused
- [ ] Manual smoke optional: Trello or ClickUp read/create

## Deny-list matcher (session creation)

Documented in `app/integrations/composio_policy.py`:

1. `GMAIL_SEND_*` / `GMAIL_REPLY_*` prefixes
2. `_BATCH_DELETE` / `_DELETE_` / trailing `_DELETE` (except `UNTRASH`)
3. `_TO_TRASH` / `EMPTY_TRASH` / `_TRASH_FILE`

Applied as per-toolkit `tools={slug: {"disable": […]}}` plus
`SESSION_PRESET_DIRECT_TOOLS` on `create_mcp_session`.

## Out of scope
- Propose-then-confirm wrapper around Composio writes (future task;
  deny-list is the MVP control)
- Removing DIY Google modules (025)
- Composio triggers / inbound webhooks
- Custom OAuth / white-label
- Expanding registry beyond 022's seven toolkits

## Depends on
- 022 (client, registry)
- 023 (real connected accounts to test against; can stub session in
  unit tests earlier)

## Log
### [PA] 2026-08-10 14:20 — Grooming
Third slice: MCP attach + sensitive-action deny-list. Writes/drafts
allowed; email send and deletes blocked.
### [PA] 2026-08-10 14:30 — Architecture review
Locked: local Google tools gated on legacy integration rows (no dual
tool surface during 024→025); MCP attach failure degrades to local-only
turn instead of failing the job; MCP PII in Logfire accepted for MVP
(revisit before real users).
### [DEV] 2026-08-10 — Implemented
MCP attach on agent turns; deny-list at session creation; legacy Google
tool gate; degrade-on-failure; unit tests. Manual Notion/Gmail smoke
left for deploy verification.
