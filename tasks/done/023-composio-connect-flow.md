---
id: 023-composio-connect-flow
feature: integrations
status: done
---

# Composio connect flow (generic `/connect/{provider}`)

## Scope
Reuse the in-chat signed-link threat model (ADR 0006): WhatsApp only ever
gets our landing URL; Composio authorize URLs are never sent raw.
Generic `/connect/{provider}` routes for every registry toolkit; success
upserts `integration` and enqueues `integration_notify`.

## Product decisions (locked)
- One connect per **toolkit slug** (`gmail`, `notion`, …) — not a bundled
  `google` super-connect; not `googlesuper`.
- `request_integration(provider)` allowlisted via 022 registry; mints
  `connect_link` with `provider`; URL
  `{APP_BASE_URL}/connect/{provider}?t=…` (10-min expiry); scoped only to
  the current turn's `contact_id`.
- Forwarded-link model preserved: URL `provider` must match
  `connect_link.provider`.

## Acceptance criteria

### Routes
- [x] `GET /connect/{provider}?t=<signed>` — validate token + nonce;
      provider match; generic landing (masked phone, registry PT copy)
- [x] `GET /connect/{provider}/start?t=<signed>` — atomically
      start/extend link; `composio` session with
      `user_id=str(contact_id)`; `session.authorize(provider)` → 302 to
      Composio redirect; `callback_url` → our success page **with the
      signed token embedded** (`callback_url = …/success?t=<signed>` —
      locked 2026-08-10: mirrors the current Google `state` pattern so
      the success route can bind account→contact without trusting
      anything from the third-party redirect)
- [x] `GET /connect/{provider}/success?t=<signed>` — validate token,
      claim the link (single atomic UPDATE, as in the DIY callback, so a
      double-fired callback loses); verify connected account for
      `user_id=str(contact_id)` + toolkit; upsert `integration`
      (`status=active`, `external_account_id` set, no local tokens);
      enqueue `integration_notify`; mark link used; **redirect (303) to
      a static success page** so reload never re-runs the one-time
      callback
- [x] Unknown/disabled provider → friendly error page, no 500
- [x] Tampered/expired/used/mismatched-provider link → friendly error page
- [x] Refactor or retire hardcoded `/connect/google` + `/oauth/google/*`
      enough that new connects do not depend on DIY Google exchange
      (full DIY deletion is 025; this task must not leave a broken
      half-path for registry providers)

### Agent + notify
- [x] `request_integration` extended to registry providers; returns
      signed `/connect/{provider}` URL
- [x] `integration_notify` copy per provider from registry (PT)

### Tests
- [x] Unit: provider mismatch on landing
- [x] Unit: success upserts `integration` with `external_account_id`, no
      refresh token stored
- [x] Unit: unknown provider rejected
- [x] Unit: success route rejects missing/tampered `t` and does not
      upsert (callback binding is the only contact identity source)

### Manual
- [ ] WhatsApp: "conecta meu Notion" → link → landing → OAuth →
      "Notion conectado ✓"
- [ ] Same for Gmail (managed auth; consent may say Composio)
- [ ] Second contact connecting the same toolkit does not attach to the
      first contact

## Out of scope
- MCP toolset on the worker (024)
- Tool allow/deny policy (024)
- Deleting `app/integrations/google.py` and local Gmail/Calendar tools
  (025)
- Composio `COMPOSIO_MANAGE_CONNECTIONS` in-chat auth (disabled in 024;
  connect stays on `request_integration` only)

## Depends on
- 022 (registry, schema, Composio client)

## Log
### [PA] 2026-08-10 14:20 — Grooming
Second slice of Composio-first epic: generic connect routes + notify.
### [PA] 2026-08-10 14:30 — Architecture review
Locked: callback binding via signed token embedded in `callback_url`
(same threat-model as DIY `state`); success route claims link + 303s to
static page.
### [Agent] 2026-08-10 — Implementation
Generic `/connect/{provider}` landing/start/success; registry-backed
`request_integration` + `integration_notify` PT copy; Composio
`find_active_connected_account_id`; DIY Google routes kept until 025;
unit tests green. Manual WhatsApp checks still open.
### [Agent] 2026-08-10 — Merged
Merged via PR #15; moved to `tasks/done/`.
