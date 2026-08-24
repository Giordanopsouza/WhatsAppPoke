0006. Google OAuth via WhatsApp one-time connect links
Status: Accepted — amended 2026-08-10 by 0008 / 025 (connect-link threat
model retained; DIY Google OAuth, Fernet refresh tokens, and local
Gmail/Calendar tools removed — Composio managed auth + MCP only)
Date: 2026-08-07

Context
WhatsApp is the only UI. There is no browser session tied to the chat
and no safe way to put a phone number into an OAuth `state` parameter
that a stranger could not forge or forward. Every contact is a tenant;
a forwarded link must never attach someone else's grant to the original
contact.

Originally this ADR also covered Fernet-encrypted Google refresh tokens
and local Calendar/Gmail clients. That credential path is gone (task
025 / ADR 0008). What remains normative is the **connect-link threat
model** used by Composio toolkit connects.

Decision
Connect SaaS accounts from WhatsApp with a one-time signed link. The
landing + claim pattern below still applies; the authorize/callback
backend is Composio (`/connect/{toolkit}`), not our Google Cloud app.

1. **Mint.** Agent tool `request_integration("<toolkit>")` creates a
   `connect_link` row (unique nonce, short TTL, provider = toolkit slug)
   and returns an absolute URL signed with `itsdangerous`
   (`CONNECT_SIGNING_KEY`). The tool binds to `ctx.deps.contact_id`
   only — never accepts a phone argument.
2. **Land.** `GET /connect/{toolkit}?t=…` validates the token + unused /
   unexpired nonce + provider match and renders a server-side page
   (masked phone, Connect). No SPA. Start extends TTL for slow consent
   and 302s to Composio managed auth with `callback_url` embedding a
   freshly signed token.
3. **Callback.** Atomically `claim_connect_link`, verify the connected
   account at Composio, upsert `integration` (`external_account_id`,
   status `active`/`revoked`), enqueue `integration_notify`, 303 to a
   success page. Confirmation WhatsApp is sent by the worker under the
   contact turn lock — not inline in the HTTP request.
4. **Credentials.** Composio owns tokens, refresh, and revocation. We
   store no refresh tokens; `FERNET_KEY` / `app/crypto.py` are gone.

Consequences
Positive:
- Works entirely from WhatsApp; no separate login product.
- Forwarded links cannot steal tenancy: one-time claim + signed state.
- Landing page gives context, branded errors, and a success screen
  before/after the provider consent UI.
- Callback stays fast and durable: notify is a job, so deploys/crashes
  do not drop the "conectado ✓" message.

Negative / tradeoffs (historical DIY path — superseded by 0008):
- Fernet key rotation / loss and Google OAuth Testing-mode limits applied
  while we stored refresh tokens locally; those tradeoffs left with 025.

Rejected alternatives:
- Redirect straight from the chat link to the IdP — no context, no branded
  errors, harder success UX; competitor pattern and our landing page win.
- Put phone / contact id in OAuth `state` without a one-time DB claim —
  link forwarding attaches the wrong account to the victim.
- Store plaintext refresh tokens (or rely only on RLS) — DB dumps and
  over-broad replicas become credential leaks.
- Mark the link used only after the IdP returns — holds a pooler
  connection across a multi-second HTTP call and allows double-fired
  callbacks to replay the authorization code.

See also: ADR 0008 (Composio-first), `docs/database.md` (`integration`, `connect_link`), tasks
`022`–`025`.
