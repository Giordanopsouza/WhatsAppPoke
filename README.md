# An Open-Source Poke Implementation for WhatsApp

> An independent implementation of the Poke-style personal AI assistant,
> purpose-built for WhatsApp.

This project reimplements the Poke experience as a WhatsApp-first, open-source
personal AI assistant. Send it a message to manage Gmail, Google Calendar,
tasks, reminders, recurring automations, and web research. Sensitive actions
are staged and require a later WhatsApp confirmation before they run.

It is an independent project, not a fork of, affiliated with, endorsed by, or
maintained by Poke or Cognition. Poke is a product of its respective owners.

## Why this Poke implementation

- **WhatsApp-first personal assistant.** No new productivity app to learn.
- **Gmail and Google Calendar.** Connect accounts through managed auth; search
  email, create drafts, check calendars, and stage event creation.
- **Tasks, reminders, and automations.** Keep a checklist, schedule a precise
  WhatsApp reminder, or run a recurring goal on an RRULE schedule.
- **Safe by design.** Email sends and calendar writes are staged with their
  exact payload and need an explicit later confirmation.
- **Built for real operations.** Twilio webhook verification, idempotent
  outbound messages, per-contact isolation, Postgres-backed background jobs,
  retries, dead letters, and PII-conscious observability are first-class.

## How it works

```text
You on WhatsApp
      │
      ▼
Twilio webhook → FastAPI → Interaction Agent
                              │
                              ├── replies on WhatsApp
                              └── starts an isolated Execution Agent
                                       │
                                       ├── Gmail / Google Calendar
                                       ├── tasks / reminders / automations
                                       └── web research
```

The Interaction Agent is the only component that speaks on WhatsApp. Longer
or tool-using work runs in an isolated Execution Agent and returns to the
conversation when it is complete. Scheduled reminders and automations are
durable Postgres jobs handled by a separate worker process.

## Stack

Python 3.12 · FastAPI · Pydantic AI · OpenRouter · Twilio WhatsApp ·
PostgreSQL/Supabase · Composio managed auth · Railway

## Run locally

### Prerequisites

- Python 3.12 and [uv](https://docs.astral.sh/uv/)
- A PostgreSQL/Supabase database
- A Twilio WhatsApp sender
- OpenRouter, Composio, Tavily, Logfire, and Sentry credentials

### Setup

```bash
git clone https://github.com/Giordanopsouza/wpp-agent-sanitized.git
cd wpp-agent-sanitized
cp .env.example .env
uv sync
uv run alembic upgrade head
```

Fill in `.env` using the notes in [`.env.example`](.env.example). Then start
the API and durable background worker in separate terminals:

```bash
uv run uvicorn app.api.main:app --reload
uv run python -m app.worker
```

For local Twilio testing, expose the API with a public HTTPS tunnel and set
your WhatsApp sender's inbound webhook to:

```text
https://<your-public-host>/webhook/twilio
```

See the [deployment guide](docs/deploy.md) for the Railway production setup
and [architecture plan](docs/plan.md) for the runtime model.

## Security and data boundaries

- A WhatsApp contact is the tenant boundary; persisted product data is scoped
  by `contact_id`.
- Twilio signature validation protects the inbound webhook.
- Gmail and Calendar access use Composio managed auth—not locally stored OAuth
  tokens.
- Email sends and calendar creation never execute in the same turn that
  requests them; they wait for a later explicit confirmation.
- Sentry receives identifiers for correlation, never WhatsApp message bodies
  or credentials.

## Project status

This is an MVP / active development implementation. The current first-party
integrations are Gmail and Google Calendar. The product surface and deployment
requirements are documented in the repository; check the architecture and task
documents before relying on it in production.

## Contributing

Issues and focused pull requests are welcome. Please read
[AGENTS.md](AGENTS.md), which explains the module boundaries, tenant and
security invariants, and the one-task-per-branch workflow.

## License

No license is currently included. Add an explicit OSI-approved license before
describing the repository as open source or accepting unrestricted reuse and
contributions.
