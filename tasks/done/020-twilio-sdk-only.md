---
id: 020-twilio-sdk-only
feature: transport
status: in-progress
---

# Twilio SDK only — remove Z-API

## Scope
Make Twilio WhatsApp the sole transport: SDK outbound, typed inbound
columns on `message`, delete Z-API route/config/module.

## Acceptance criteria
- [x] No `app/zapi.py`, no `/webhook/zapi/{token}`, no `ZAPI_*` / `WEBHOOK_TOKEN` in config
- [x] `send_text` uses Twilio `Client.messages.create` (via `asyncio.to_thread`)
- [x] Alembic migration + ORM expose typed Twilio columns on `message`
- [x] Inbound persists typed fields; `agent_turn` enqueued only for non-empty body
- [x] Fixtures/tests cover Twilio text + media parse and mocked SDK send
- [x] ADR 0007 + AGENTS/plan/deploy updated for Twilio-only

## Out of scope
- Agent/LLM media processing
- Typing indicators
- JSON `provider_payload` column
- Purchasing/provisioning the production WhatsApp number

## Log
### [PA] 2026-08-08 — Implement
Branch `020-twilio-sdk-only` from the plan in
`.cursor/plans/twilio_sdk_only_2378d54d.plan.md`.
