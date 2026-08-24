# Deploy — Railway

One Docker image, three Railway services in one project/environment
(`production`). All build from the same repo and the same `Dockerfile`;
only the start command and the config-as-code file differ.

| Service | Config file | Start command | Port |
|---|---|---|---|
| `api` | `railway.api.json` | `sh -c 'uvicorn app.api.main:app --host 0.0.0.0 --port ${PORT:-8000}'` | `8000` (public) |
| `worker` | `railway.worker.json` | `python -m app.worker` | none |
| `analytics` | `railway.analytics.json` | `sh -c 'streamlit run analytics/dashboard.py --server.port ${PORT:-8501} …'` | `8501` (public) |

The `sh -c` wrapper is required: Railway execs the start command without
a shell, so a bare `$PORT` reaches uvicorn as a literal string and the
container dies before binding. Set `PORT` explicitly per public service
(`8000` on api, `8501` on analytics — Railway does not inject it) and pin
the custom domain's target port to the same value. See ADR 0004.

Migrations run as the **api** service's `preDeployCommand`
(`alembic upgrade head`) — Railway runs it to completion before the new
deployment starts serving. The worker deliberately does **not** run
migrations: two concurrent `alembic upgrade head` runs would race.

## One-time setup

1. **Project + services**

   ```bash
   railway login
   railway init --name wpp-agent
   railway add --service api
   railway add --service worker
   railway add --service analytics
   ```

2. **Point each service at its config file.** In Railway → service →
   Settings → Config-as-code file path:
   - `api` → `railway.api.json`
   - `worker` → `railway.worker.json`
   - `analytics` → `railway.analytics.json`

   All three services also need Settings → Source → this GitHub repo, branch
   `main`. (Config as code cannot set the source; that is a dashboard/IaC
   concern.)

3. **Variables.** Set the full agent set from `.env.example` on **api and
   worker** — `app/config.py` validates every var at import time, so a
   missing one crash-loops the process even if that service never uses it.
   The analytics service only needs `DATABASE_URL` plus `ANALYTICS_*`.

   ```bash
   railway variables --service api --set DATABASE_URL=... --set OPENROUTER_API_KEY=...
   railway variables --service worker --set DATABASE_URL=... --set OPENROUTER_API_KEY=...
   railway variables --service analytics --set DATABASE_URL=... --set ANALYTICS_USER=... --set ANALYTICS_PASSWORD=...
   ```

   `DATABASE_URL` must be the Supabase **pooler** URL (port 6543) with the
   `postgresql+asyncpg://` scheme.

   `OPENROUTER_CHAT_MODEL` powers Interaction (e.g.
   `google/gemini-3.7-flash:nitro`); `OPENROUTER_EXEC_MODEL` powers the
   detached Execution agent. Both are OpenRouter slugs; the `:nitro` variant
   prioritizes the highest-throughput compatible provider. They are required
   on **api and worker**. `GOOGLE_API_KEY` / `GEMINI_*` are gone.

   `COMPOSIO_API_KEY` supports managed-auth authorization and the fixed
   authenticated-proxy calls made by owned tools (ADR 0015); no service
   creates MCP sessions. In the Composio dashboard, set project log storage
   to **Don't store data** (LGPD). Only Gmail and Google Calendar have owned
   tools in this release; a connected provider without one is not attached to
   Execution.

4. **Custom domain.** api service → Settings → Networking → Custom Domain
   → `api.<domain>`. Railway returns a CNAME target
   (`<something>.up.railway.app`). At the DNS provider create:

   ```
   api                    CNAME    <target>.up.railway.app    (proxy ON if Cloudflare)
   _railway-verify.api    TXT      railway-verify=<value from Railway>
   ```

   Both records are required — without the TXT record Railway returns
   404 instead of routing to the service.

   On Cloudflare the CNAME should be **proxied (orange cloud)** and
   SSL/TLS mode set to **Full** — not Full (Strict), which returns 526
   during certificate renewals, and not Flexible, which causes a
   redirect loop. Railway will keep showing the CNAME as
   `REQUIRES_UPDATE` because it sees Cloudflare IPs; that is cosmetic.
   If the certificate stalls on "validating challenges", flip to grey
   cloud until it goes green, then back to orange. Do **not** delete and
   re-add the domain — Let's Encrypt rate-limits at 5 certs per domain
   per week. See ADR 0005.

   Railway issues the TLS cert once the CNAME resolves. The apex and
   `www` stay untouched for a future marketing site.

5. **Point the Twilio WhatsApp webhook** (sandbox or sender) at:

   ```
   https://api.<domain>/webhook/twilio
   ```

   Use HTTP POST. Auth is `X-Twilio-Signature` (no path token). After
   schema changes, confirm `alembic upgrade head` ran via the api
   `preDeployCommand`.

6. **Reminder Utility template.** Create a WhatsApp Utility Content
   Template in Twilio (Content Template Builder) whose body exposes a
   single variable `{{1}}` for the stored reminder body. Submit for
   Meta approval, then set `TWILIO_REMINDER_CONTENT_SID` (HX…) on **both**
   services.    Inside the 24h customer-service window the worker sends
   free-form text; outside it, it sends this template.

7. **Automation & Action Utility templates.** Create WhatsApp **UTILITY**
   Content Templates (`twilio/text` or `twilio/card`, language `pt_BR`)
   with variable `{{1}}` for proactive notifications and action confirmation
   outside the 24h customer-service window:
   - `TWILIO_AUTOMATION_CONTENT_SID` (HX…) for background task completion.
   - `TWILIO_ACTION_CONTENT_SID` (HX…) for actions needing confirmation.
   Submit for Meta approval and configure on api and worker.

## Analytics dashboard

A read-only Streamlit dashboard over the same database (`analytics/`). It
does **not** import `app.core.config` — that Settings validates every agent var at
import time, so reusing it would crash-loop a service that only reads
Postgres. `analytics/settings.py` reads its own short list instead:

| Var | Required | Notes |
|---|---|---|
| `DATABASE_URL` | yes | same pooler URL as api/worker |
| `ANALYTICS_USER` | yes | login user |
| `ANALYTICS_PASSWORD` | yes | min 12 chars |
| `ANALYTICS_EXCLUDE_PHONES` | no | comma-separated test numbers; `+E.164` or digits-only |
| `ANALYTICS_TZ` | no | defaults to `America/Sao_Paulo` |

Set `PORT=8501` as a service variable (Railway does not inject it) and pin
the custom domain's target port to the same value — same constraint as api,
see ADR 0004. Health check is Streamlit's own `/_stcore/health`.

Auth is a user/password gate in the app (`hmac.compare_digest`), not SSO. It
keeps casual visitors out; the dashboard only ever renders aggregates —
message bodies are never selected and phone numbers are masked.

Local run:

```bash
uv run streamlit run analytics/dashboard.py
```

## Verify

```bash
curl https://api.<domain>/health          # {"status":"ok"}
curl https://analytics.<domain>/_stcore/health  # ok
railway logs --service worker             # worker_started
```

## Interaction runtime

Every non-empty inbound is persisted, receives HTTP 200, and then runs one
Interaction event. There is no classifier, acknowledgement bubble, contact
feature flag, queued conversation job, or daily briefing knock. Worker kinds
are reminders, automations, and integration notifications.

Before release, exercise chat, five-bubble fuse, Gmail read/draft/confirm,
Calendar read/create/confirm, reminder, automation, overlapping
inbound, timeout, and fallback. Inspect PII-safe Logfire spans: `webhook`,
`interaction`, `interaction.first_visible_outbound`, `execution`,
`proxy_tool`, `execution.result_reentry`, `pending_action_confirmation`,
execution timeout/abandonment, and duplicate suppression.

Then, manually verify a WhatsApp message gets a reply and restart the worker
while a reminder/automation job is pending: the Postgres queue is claimed with
`FOR UPDATE SKIP LOCKED`, and a stale lock is recovered without duplicate
delivery.

## Notes

- `healthcheckPath: /health` gates the api rollout: Railway keeps the old
  deployment serving until the new one answers 200.
- The worker uses `restartPolicyType: ALWAYS` (it is a loop, not a
  request server); the api uses `ON_FAILURE`.
- Scale the worker with replicas — per-contact serialization is enforced
  by the Postgres advisory lock in `app/db.py`, not by having one worker.
