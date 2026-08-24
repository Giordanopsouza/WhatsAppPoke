0008. Composio-first integrations (managed auth, MCP tools)
Status: Accepted
Date: 2026-08-10

Context
ADR 0006 shipped a DIY Google OAuth path: our own Google Cloud app,
Fernet-encrypted refresh tokens in `integration`, per-contact advisory-
locked refresh, and hand-written Gmail/Calendar tools
(`app/integrations/google.py`). It works, but every new SaaS the
assistant should reach (Notion, Trello, ClickUp, Drive, Sheets, …)
repeats the whole cost: a new OAuth app, provider verification, token
lifecycle + encryption + rotation, revoke mapping, and bespoke agent
tools per API. Google alone also keeps us in OAuth Testing mode
(100 test users, unverified warning) until a verification process we
have not scheduled.

There are no production users and no rows worth migrating, so a clean
cut is cheap now and expensive later. Composio offers managed OAuth
(consent, token storage, refresh, revocation handled provider-side) and
an MCP server per user/toolkit set that Pydantic AI can attach as a
toolset — replacing both the credential store and the hand-written
tools with one integration backend.

Decision
Composio becomes the **only** integration backend. DIY Google OAuth,
local token storage, and the local Gmail/Calendar tools are removed
(task 025). Concretely:

1. **Managed auth for MVP.** Connect consent screens may show
   "Composio"; we run no provider OAuth apps of our own. Custom OAuth /
   white-label consent is post-MVP, gated on brand need or managed-auth
   quota limits. No `googlesuper` in MVP (managed auth can hit Google
   "App is blocked" on broad scopes); connect is per toolkit slug.
2. **Tenancy at Composio.** `user_id = str(contact_id)` on every
   session — never a shared "default" user. The MVP registry allowlists
   seven managed-auth toolkits: `gmail`, `googlecalendar`,
   `googledrive`, `googlesheets`, `notion`, `trello`, `clickup`.
3. **Connect flow keeps the ADR 0006 threat model.** WhatsApp only ever
   receives our signed one-time link (`/connect/{provider}?t=…`,
   10-min TTL, provider must match `connect_link.provider`). The
   `callback_url` handed to `session.authorize` embeds the same signed
   token, so the success route binds account→contact without trusting
   anything in the third-party redirect. Success claims the link
   atomically (double-fire loses), verifies the connected account,
   upserts `integration` (`external_account_id` = `ca_…`, no local
   tokens), enqueues `integration_notify`, and 303s to a static page.
4. **Schema slims to a pointer.** `integration` keeps
   `unique(contact_id, provider)` and gains `external_account_id`;
   `refresh_token_enc` and `scopes` are dropped — Composio owns scopes,
   tokens, refresh, and revocation. No `auth_backend=local` hedge.
5. **Agent tools via MCP, policy at session creation.** Per agent turn,
   if the contact has active integrations the worker creates a session
   (`toolkits=[active slugs]`, `mcp=True`, `manage_connections=False`)
   and attaches `MCPToolset` (`pydantic-ai-slim[mcp]`) alongside local
   tools. MCP bypasses SDK `before_execute` modifiers, so the
   sensitive-action policy is a **tool filter at session creation**.
   ADR 0009: **enable allowlist** of the slugs WhatsApp needs — not the
   full catalog minus a deny-list. `manage_connections=False` keeps the
   agent from minting raw Composio connect URLs — connect stays on
   `request_integration` + our signed links only.
6. **Failure and transition policy.** If MCP session creation fails or
   times out, the turn degrades to local tools with a warning — Composio
   downtime must not dead-letter agent turns. Task 025 removed DIY Google
   tools and the legacy-row gating shim; a stale `provider=google` row is
   ignored (no local Google surface).
7. **Privacy.** Composio project log storage is set to "Don't store
   data" (no tool payloads retained provider-side). Account deletion
   (task 021) calls `connected_accounts.delete(external_account_id)`
   before purging local rows. MCP tool args/results flowing into Logfire
   spans are an accepted risk for MVP (no users, controlled access) —
   revisited with redaction or `instrument=False` before real users.

Consequences
Positive:
- One credential backend instead of N OAuth apps, verifications, token
  stores, and revoke mappings; adding a toolkit is registry + dashboard
  work, not a new auth stack.
- No refresh tokens at rest: `FERNET_KEY` / `app/crypto.py` were removed
  with DIY Google (025); Composio owns grants.
- Tool surface scales without hand-written API clients; Pydantic AI
  gets typed MCP tools per contact, per turn.
- Tenancy is enforced at the vendor boundary (`user_id`) as well as in
  our DB (`contact_id` + RLS).
- Connect UX keeps every protection that made 0006 safe: signed
  one-time links, provider match, atomic claim, worker-sent confirm.

Negative / tradeoffs:
- New runtime dependency on a third party in the agent hot path; every
  turn with integrations pays session-creation latency, mitigated by
  the degrade-to-local policy.
- Consent screens show Composio branding until we pay for / configure
  white-label; managed-auth shared quota is a ceiling we do not control.
- Tool policy lives in an enable slug list, not code we fully control —
  a Composio rename of an enabled tool breaks that action until we
  update the list. Direct Tools plus a **disable** list dumped ~180
  schemas per turn (~150k input tokens); remediated in ADR 0009
  (enable-only allowlist now, agent/subagent split later).
- MCP payloads in Logfire are unredacted until the telemetry task
  lands; acceptable only while there are no real users.
- Per-turn sessions are not reused; if latency becomes a product
  complaint we will need session caching with careful TTL/tenancy.

Rejected alternatives:
- Keep DIY Google and add providers one by one — each provider re-pays
  OAuth app + verification + token lifecycle + bespoke tools; the
  marginal cost never drops.
- Hybrid hedge (`auth_backend=local|composio` rows coexist long-term) —
  two credential models to secure and test, with zero users to protect;
  clean cut wins.
- Legacy `composio-pydanticai` SDK — superseded by the official v3 SDK +
  native `MCPToolset`; one less shim to track.
- `googlesuper` bundled Google connect — managed auth risks Google's
  "App is blocked" on broad scopes; per-toolkit connects are granular
  and recoverable.
- Post-hoc enforcement (`before_execute` modifiers) for send/delete —
  MCP calls bypass SDK hooks; deny must happen at session creation.
- Agent-driven connect via Composio `manage_connections` — the model
  would mint raw third-party URLs into WhatsApp, breaking the 0006
  threat model; connect stays on our signed links.
- Custom OAuth / white-label now — brand polish before first user.

See also: ADR 0006 (connect-link threat model retained; DIY token
storage/refresh superseded), ADR 0009 (tool-context allowlist; later
execution subagent), tasks `022-composio-foundation`,
`023-composio-connect-flow`, `024-composio-mcp-agent`,
`025-composio-kill-diy-google`, `021-delete-account`.
