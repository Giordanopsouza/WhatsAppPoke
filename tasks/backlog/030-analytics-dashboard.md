# 030 — Analytics dashboard

**Status:** deployed (Railway service `analytics`), pending merge to `main`

## Migration preflight

Before implementation, inspect the relevant sections of `docs/plan.md`, the governing ADRs, this task, and its directly dependent or consuming tasks. Record:

- target end-state and contracts introduced here;
- legacy code allowed only as a temporary rollback bridge;
- legacy imports, data paths, and behaviors forbidden in new code;
- the task that removes each temporary bridge;
- an architecture test or CI check that enforces the boundary.

## Why

No visibility into how contacts actually use the agent — only ad-hoc SQL.
Need day/week activity, per-contact engagement, and a read on where the
integration funnel leaks.

## What

Read-only Streamlit dashboard over the same Postgres, deployed as a third
Railway service in the existing project.

- `analytics/queries.py` — SQL only; aggregates, never message bodies
- `analytics/dashboard.py` — Streamlit UI + user/password gate
- `analytics/settings.py` — its own config (5 vars), not `app.config`
- `railway.analytics.json` — config-as-code for the service

## Decisions

- **No SQL views, no migration.** The dashboard is the only consumer, so the
  SQL lives in `analytics/queries.py`. Production schema untouched.
- **Separate settings.** `app.config.Settings` validates every agent var at
  import; reusing it would crash-loop a service that only reads Postgres.
- **Days cut in `America/Sao_Paulo`.** A UTC cut moves the boundary to 21h
  local and splits evening conversations across two days.
- **"Active" means inbound.** A contact that only received a reminder ping
  is not engaged.
- **`prepare_threshold=None`** on the psycopg engine — the Supabase pooler
  runs in transaction mode, where a server-side prepared statement can land
  on a different backend than the one that executes it.

## Acceptance criteria

- [x] All queries run against production without error
- [x] Dashboard renders headlessly with no exception (`streamlit.testing`)
- [x] Login gate rejects wrong credentials
- [x] No message body or full phone number rendered anywhere
- [x] Deployed to Railway with a public URL behind login
- [ ] Custom domain `analytics.<domain>` pointed and certificate issued
- [ ] Merged to `main` and service switched to GitHub source

## Follow-ups

- Rotate the Postgres password (it was pasted in plaintext in a chat).
- Set `ANALYTICS_EXCLUDE_PHONES` once test numbers are identified — with
  ~17 contacts, a few test numbers meaningfully skew every metric.
- Cohort retention (D1/D7/D30) only becomes meaningful around 200+ users.
