---
id: 006-deploy-two-services
feature: infra
status: done
---

# Deploy: one image, two Railway services

## Scope
Production deploy on Railway: a single Docker image with two start
commands — `api` (uvicorn) and `worker` (queue consumer) — plus
migrations as a release step.

## Acceptance criteria
- [x] `Dockerfile` using `uv` for dependency install; no dev extras in
      the image
- [x] `api` service: `uvicorn app.main:app --host 0.0.0.0`, healthcheck
      on `GET /health`
- [x] `worker` service: `python -m app.worker`, no exposed port
- [x] Release command runs `alembic upgrade head` before new code
      starts serving
- [x] `.env.example` documents every required var for both services
- [x] Custom subdomain `api.<domain>` on the `api` service: CNAME at the
      DNS provider → Railway target, Railway-issued TLS
- [x] Z-API webhook URL repointed from the cloudflared tunnel to
      `https://api.<domain>/webhook/zapi/{token}`
- [x] Manual: send a message → reply works from production
- [ ] Manual: trigger a redeploy mid-turn → no turn is lost (job retried
      by the queue) — **not tested; carried to task 017**

## Out of scope
- Apex/`www` marketing site (the apex stays free for it later)
- Autoscaling rules, multi-region
- Staging environment

## Log
### [PA] 2026-08-05 15:45 — Grooming
Created from `docs/plan.md` Phase A. Depends on 002.
### [PA] 2026-08-05 16:30 — Custom domain pulled into scope
Owner has a domain. `api.<domain>` subdomain gives a stable webhook URL
and a trustworthy identity for the Google consent screen (task 008);
apex reserved for a future marketing site.
### [SWE] 2026-08-07 11:35 — Repo-side deploy artifacts
Added `Dockerfile` (two-stage, `uv sync --locked --no-dev`, non-root,
`.venv/bin` on PATH), `.dockerignore`, `railway.api.json` (uvicorn on
`$PORT`, healthcheck `/health`, `preDeployCommand: alembic upgrade head`),
`railway.worker.json` (`python -m app.worker`, no port, restart ALWAYS),
rewrote `.env.example` for both services, and wrote `docs/deploy.md` as
the runbook. `railway.json` fallback chosen over TypeScript IaC — no
TypeScript in this repo (see `use-railway` → `references/iac.md`).
Migrations run only on `api` so two services never race on
`alembic upgrade head`. Remaining criteria (Railway project, variables,
custom domain + CNAME, Z-API repoint, manual redeploy-mid-turn test)
need account access and are pending.
### [SWE] 2026-08-07 17:30 — Deployed; three platform failures fixed
Both services green in project `empowering-art` / `production`.

Three failures, all Railway platform behaviour rather than app bugs;
each is recorded in ADR 0004 because the fix looks like noise without
the context:
- Build failed on both services. The Metal builder rejects
  `--mount=type=bind` outright and requires an `id` on
  `--mount=type=cache`, which one Dockerfile shared by two services
  cannot supply. Replaced with a plain `COPY` of the lock files (PR #3).
- `api` failed its healthcheck. Railway execs `startCommand` without a
  shell, so uvicorn received the literal string `$PORT` and died before
  binding — the dashboard blamed "Network › Healthcheck" while nothing
  was ever listening. Wrapped in `sh -c` (PR #4).
- Railway never injected `PORT`, before or after the domain was
  attached. Set `PORT=8000` explicitly and pinned the domain's target
  port to match.

Also wired each service's config-as-code path in Railway settings
(`railway.api.json` / `railway.worker.json`). They were unset, so both
services reported `Builder: RAILPACK` and ignored the config files
entirely — the Dockerfile was only being picked up by auto-detection,
meaning `api` would have run the image's `CMD` (`python -m app.worker`)
with no healthcheck and no migration pre-deploy step.

Domain: `api.gglabs.ventures`, Cloudflare-proxied, SSL mode Full,
certificate VALID. Choice of subdomain-over-apex, orange cloud, and
Full-over-Full-Strict is recorded in ADR 0005; `docs/deploy.md` step 4
previously said "proxy OFF if Cloudflare" and omitted the required TXT
record, both now corrected.

Verified in production: `GET /health` 200 through all three Cloudflare
edge IPs; webhook returns 404 on a bad token and 200 on a valid one;
real `POST /webhook/zapi/…` traffic from Z-API arriving with 200s; no
errors in the worker's logs.

Not verified: the redeploy-mid-turn resilience test. Task 017 already
owns that concern — it names the same unchecked manual criterion from
`tasks/done/002-worker-process.md` and closes the duplicate-reply
windows that test would exercise. Left unchecked here rather than
claimed.

Follow-ups found while deploying, none blocking:
- The worker's idle poll (`POLL_IDLE_SECONDS = 0.5`) opens a fresh
  connection roughly twice a second against the Supabase pooler, and
  Logfire's SQLAlchemy instrumentation emits a span for every one. The
  comment at `app/worker.py:243` intends to keep empty polls out of
  Logfire, but the auto-instrumentation bypasses that.
- Local and Railway share byte-identical `LOGFIRE_TOKEN` and
  `SENTRY_DSN`, and neither `logfire.configure()` nor `sentry_sdk.init()`
  sets `environment` — dev and production telemetry are indistinguishable
  in both tools.
- Dead variables on both services: `FIREWORKS_API_KEY` / `FIREWORKS_MODEL`
  (stale since ADR 0003) and `DATABASE_PASSWORD`, which nothing reads and
  which duplicates the secret already embedded in `DATABASE_URL`.
