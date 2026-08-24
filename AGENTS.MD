# wpp-agent — Agent Guide

Map first. Open a linked file only when the task needs it.

| Doc | Path | Audience | Use for |
|---|---|---|---|
| Architecture & decisions | `docs/plan.md` | Humans + agents | Product shape, tenancy, deferred work |
| Database | `docs/database.md` | Humans + agents | Tables, ER, RLS conventions |
| Deploy | `docs/deploy.md` | Humans + agents | Railway, env, three-process runtime |
| Execution tasks | `tasks/NNN-*.md` | Humans + agents | Atomic work, acceptance criteria, status |
| ADRs | `docs/adr/` | Humans + agents | Why we chose X over Y |
| Glossary | `docs/glossary.md` | Humans + agents | Canonical terms + exclusions |

## Identity

Personal WhatsApp assistant (MVP): Twilio inbound → FastAPI **api** persists +
200 → one **Interaction** event. Interaction is the only WhatsApp speaker; it
can send an idempotent visible message or start a detached **Execution** run
with owned tools. Execution records its result and re-enters Interaction.
**worker** keeps durable background jobs (reminders, automations, integration
notifications, retries, dead letters). Tenant = one WhatsApp **contact**.
ADRs 0014–0017.

## Commands

```bash
uv run uvicorn app.api.main:app --reload   # api / webhook :8000
uv run python -m app.worker            # durable background-job worker
uv run streamlit run analytics/dashboard.py  # analytics dashboard :8501
uv run alembic upgrade head            # apply migrations to Supabase
uv run pytest                          # when tests exist
```

Package manager: **uv**. Never pip-install outside `pyproject.toml`.

## Module boundaries

| Module | Owns | Must NOT know |
|---|---|---|
| `app/api/` | Webhook, Composio connect routes, fast-200, persist inbound; after 200: contact-scoped Interaction event | Tools / MCP; Twilio webhook payload stays in `twilio_wa` |
| `app/connect/pages.py` | Server-rendered connect/success/error HTML | DB queries, Composio SDK |
| `app/integrations/composio.py` | Composio managed-auth and connected-account verification | WhatsApp, agent prompts |
| `app/worker/` | Claim/execute durable background jobs, retries, dead-letter | Twilio webhook payload shape |
| `app/transport/twilio_wa.py` | Parse inbound, `send_text`, signature check — Twilio WhatsApp wire only | DB queries, agent logic, history |
| `app/agent/` | Interaction + detached Execution agents, owned/local tools, system prompts | WhatsApp wire format, phone numbers |
| `app/db/` | Engine, sessions, upsert/insert/queue queries | HTTP, LLM |
| `app/database/models/` | ORM models mirroring Alembic | Business logic |
| `app/core/config.py` | Env validation via pydantic-settings | — |
| `analytics/` | Read-only Streamlit dashboard (aggregates, login gate) | Agent, Twilio, webhooks; must not import app settings |

## Invariants agents can't infer

- **api ≠ worker.** Good: webhook verifies, persists, returns 200; Interaction and best-effort detached Execution run after 200 on the api; worker owns durable background jobs. Bad: LLM or tools *inside* the Twilio request; putting ordinary inbound conversation work on the job queue.
- **No sliding debounce.** Interaction starts immediately after the 200 and serializes per contact through its advisory lock. Durable background jobs run at their scheduled `run_at`. Bad: `run_at = now() + 3s` silence window (`docs/plan.md` still mentions debounce in places — ADR wins).
- **Tenant == contact.** Every new table gets `contact_id` + the existing RLS / revoked Data API pattern. Good: isolation in the DB. Bad: “current user” inferred only in app code.
- **Integrations use Composio managed auth (ADR 0015).** Connect via signed `/connect/{toolkit}` links; Gmail/Calendar business tools use fixed authenticated-proxy requests with code-owned schemas. Interaction never attaches SaaS tools; Execution never attaches MCP. Sensitive writes stage a `pending_action` and require a later inbound confirmation. No DIY Google OAuth or local Gmail/Calendar clients.
- **Schema = Alembic.** `DATABASE_URL` is `postgresql+asyncpg://` via Supabase **pooler** (`:6543`). Good: migration first. Bad: hand-editing prod schema or using the direct `5432` URL in app config.
- **Sentry stays PII-free.** Tag `contact_id` / `provider_message_id`; never send WhatsApp bodies or tokens to Sentry. Logfire captures agent/LLM/tool content (including message text) so Runs are inspectable.
- **Twilio is the only transport** (ADR 0007). Good: `twilio_wa` owns wire format. Bad: reintroducing Z-API or a second webhook shape.

## Principles

- Prefer removing instructions over adding; keep this file a map.
- Simplest readable change; no abstraction without a second concrete caller.
- New domain term or non-obvious decision → update `docs/glossary.md` and/or add an ADR **in the same PR**.

## Anti-patterns

- **Over-engineering:** complexity before need → start simple. Good: Postgres `job` table you already own. Bad: Redis/Temporal/LangGraph “for durability” while the queue meets the need; interfaces with one caller; speculative folders for deferred features.

## Git workflow

One task → one branch → one PR → merge to `main` → delete branch.

1. `git checkout main && git pull --ff-only`
2. `git checkout -b NNN-slug` (match `tasks/NNN-*.md`)
3. Implement, commit, push, open PR, merge
4. Delete the branch; start the next task from fresh `main`

Do not stack tasks on a long-lived branch. Worktrees only for parallel tasks.
