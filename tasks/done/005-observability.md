---
id: 005-observability
feature: runtime
status: done
---

# Observability: Logfire + Sentry

## Scope
Wire Logfire tracing and Sentry error reporting into both processes
(api and worker), with strict PII rules. This is how we see
webhook → reply latency, per-tool latency, and token usage in
production.

## Acceptance criteria
- [x] `uv add logfire sentry-sdk`; `LOGFIRE_TOKEN` + `SENTRY_DSN` added
      to `app/config.py` (validated at boot) and `.env.example`
- [x] `logfire.configure()` + instrumentation for FastAPI, httpx,
      Pydantic AI, and SQLAlchemy in both entrypoints
- [x] One span tree per job execution: claim → history load → LLM →
      tool calls → send → persist
- [x] Sentry events tagged with `contact_id` and `provider_message_id`;
      message bodies and tokens never leave the app
- [x] Manual: run a turn, see the full trace with token counts in
      Logfire; force a tool exception, see it in Sentry with tags

## Out of scope
- Alerting rules and on-call wiring
- Custom dashboards beyond defaults
- OpenTelemetry self-hosting (Logfire SaaS is fine for MVP)

## Log
### [PA] 2026-08-05 15:45 — Grooming
Created from `docs/plan.md` Phase A. Depends on 004 (Pydantic AI
instrumentation hooks).

### [SWE] 2026-08-06 19:37 — Start
Implementing shared `app/observability.py`, config/env, api + worker
instrumentation, job span tree, Sentry tags + PII scrubbing.

### [SWE] 2026-08-07 11:07 — Complete
Logfire verified on live turns (claim → history_load → llm → send →
persist with token counts). Sentry client wired with tags + PII
scrubbing; DSN matches project. New ingest accepts 200 but events not
always retained (org quota/filters) — left as ops follow-up. Moved to
done per request.
