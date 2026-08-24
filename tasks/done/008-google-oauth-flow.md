---
id: 008-google-oauth-flow
feature: integrations
status: done
---

# Google OAuth connect flow

## Scope
The competitor-style in-chat connection flow: agent emits a one-time
signed link, the user lands on **our** connect page (context + Connect
button), consents on Google, the callback stores the encrypted refresh
token, and the worker confirms over WhatsApp. Multi-tenant from the
start — every contact connects their own account.

## Google Cloud console (manual, do first)
- [x] Project created; Gmail API + Calendar API enabled
- [x] OAuth consent screen (External) with app name, support email,
      domain; scopes `gmail.readonly`, `gmail.send`, `calendar.events`
- [x] OAuth client (type **Web application**) with redirect URIs
      `https://api.<domain>/oauth/google/callback` (task 006's
      subdomain) and `http://localhost:8000/oauth/google/callback`
- [x] App stays in **Testing mode** (100 test users, "unverified app"
      warning is expected — fine for MVP; verification is deferred per
      `docs/plan.md`); early users added as test users by email
- [x] `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` / `CONNECT_SIGNING_KEY`
      in `.env` + `app/config.py` (plus `APP_BASE_URL` for absolute links)

## Acceptance criteria
- [x] Migration: `connect_link` (`contact_id` FK, `nonce` unique,
      `expires_at`, `used_at` null, timestamps) — one-time links, so a
      forwarded link can't attach a stranger's Google account to a
      contact; RLS pattern per `28b0ac108edc`
- [x] Agent tool `request_integration(provider)` mints a link **only
      for the current turn's contact** (never accepts a phone/number
      argument) and replies with it (10-min expiry)
- [x] `GET /connect/google?t=<signed>` — validates the itsdangerous
      token (unused, unexpired nonce) and renders the landing page:
      masked phone being connected, scopes explained in plain language,
      Connect button
- [x] Connect button → `GET /oauth/google/start` → 302 to Google with
      `state` = nonce, `access_type=offline`, `prompt=consent`
      (refresh token guaranteed)
- [x] `GET /oauth/google/callback` — verifies `state`, exchanges the
      code, encrypts + upserts the `integration` row, marks the link
      used, enqueues an `integration_notify` job, renders the success
      page ("volta pro WhatsApp")
- [x] Pages are server-rendered template strings in
      `app/connect_pages.py` (`HTMLResponse`) — no frontend framework
- [x] Worker handler for `integration_notify`: sends the WhatsApp
      confirmation ("Gmail conectado ✓ tenta: 'tenho email não lido?'")
- [x] Tampered/expired/used link → friendly error page, no token
      stored, no 500
- [ ] Manual: full round trip from a WhatsApp message to the
      confirmation reply; token is encrypted at rest

## Out of scope
- Token refresh logic and `invalid_grant` handling (task 009)
- Google app verification / leaving Testing mode (post-launch)
- Multiple Google accounts per contact
- Providers other than Google

## Log
### [PA] 2026-08-05 15:45 — Grooming
Created from `docs/plan.md` Phase B. Depends on 007.
### [PA] 2026-08-05 16:15 — Refined after competitor review
Adopted the landing-page pattern (link → our connect page → Google →
success page) instead of redirecting straight to Google: better context,
branded errors, success screen, future connections hub. Added one-time
`connect_link` nonces (link-forwarding threat model), Google Cloud
console setup section, Testing-mode/100-test-user reality note, and the
rule that `request_integration` mints links only for the current
contact.
### [SWE] 2026-08-07 17:15 — Start
Implementing `connect_link` migration, OAuth routes, agent tool,
worker notify handler, and config (`APP_BASE_URL` + Google OAuth vars).
### [SWE] 2026-08-07 17:30 — Complete
Migration `a1c8e4f92b07` applied. Unit tests for signing/masking/authorize
URL pass. Manual WhatsApp round trip still pending (needs deploy +
Google test user).
### [SWE] 2026-08-07 19:35 — Review fixes
Code review of PR #6. No schema change; nine fixes:
- `integration_notify` now runs under `contact_turn_lock` and the agent-turn
  "already replied" guard is gated on `attempts > 0` — the confirmation was
  silently swallowing a user message that arrived alongside it.
- `integration_notify` checks `outbound_exists_since` before sending, so a
  failed persist no longer re-sends the confirmation on every retry.
- Callback claims the link atomically (`claim_connect_link`) *before* the
  token exchange: no replayed authorization code, and no pooler connection
  held open across the call to Google.
- `state` is now the signed token, verified in the callback against
  `CONSENT_MAX_AGE_SECONDS`; the bare nonce is no longer trusted.
- `/oauth/google/start` extends the link to `CONNECT_CONSENT_TTL` (30 min) so
  a slow consent screen can't expire an authorization already granted.
- Callback 303s to `/connect/google/success`, so a reload no longer shows
  "link inválido" after a successful connect.
- `mask_phone` no longer exposes most digits (or a fake `+CC`) on short numbers.
- `integration_notify` payload carries `phone`, so the dead-letter fallback
  can reach the user.
- `recover_stale_jobs` counts the crashed run as an attempt; `unsign_connect_token`
  catches `BadData` so a malformed token can't 500.
