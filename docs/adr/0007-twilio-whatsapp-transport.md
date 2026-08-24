0007. Twilio WhatsApp as the sole transport
Status: Accepted
Date: 2026-08-08

Context
Z-API blocked the operator's WhatsApp number, making the legacy transport
unusable in practice. Twilio is more reliable for this product: official
webhook signature validation, a maintained Python SDK, delivery/status
visibility, and a clear path from sandbox to a purchased WhatsApp sender.

We will provision a new WhatsApp-enabled number on Twilio rather than
trying to reuse the number blocked on Z-API. Dual-transport (keeping a
Z-API rollback route) adds config surface and code paths for a channel we
can no longer operate.

Decision
Twilio WhatsApp is the only transport.

1. **Inbound.** `POST /webhook/twilio` accepts Messaging form posts,
   validates `X-Twilio-Signature`, parses the wire format in
   `app/twilio_wa.py`, upserts `contact` by digits derived from `From`,
   and persists typed Twilio fields on `message`.
2. **Outbound.** The worker sends via the Twilio Python SDK
   (`Client.messages.create`) wrapped in `asyncio.to_thread` so the
   sync client does not block the event loop.
3. **Remove Z-API.** Delete `app/zapi.py`, the `/webhook/zapi/{token}`
   route, and `ZAPI_*` / `WEBHOOK_TOKEN` settings. No dual-transport and
   no rollback route.
4. **Schema.** Nullable typed columns on `message` for AccountSid,
   From/To, media (`NumMedia`, `MediaUrl0`, `MediaContentType0`), WaId,
   SmsStatus, ApiVersion, NumSegments, and ProfileName. Outbound rows
   leave these NULL. Contact identity stays digits-only `contact.phone`
   from `From` (not `WaId` as PK).
5. **Agent scope.** Media inbound is persisted for a future feature;
   `agent_turn` is enqueued only when `Body` is non-empty. History load
   for the LLM also skips empty bodies so media-only rows cannot become
   the prompt (until vision fills them in).
6. **Sender.** Configured in `TWILIO_WHATSAPP_FROM` (sandbox now;
   purchased number later).

Consequences
Positive:
- One transport, one auth model (`X-Twilio-Signature`), one SDK.
- Typed webhook columns avoid a JSON blob and keep queries simple.
- Digits-only phones remain compatible with existing contact rows.
- Media can land in the DB without forcing the LLM path to handle it yet.

Negative / tradeoffs:
- Depends on a Twilio account and WhatsApp compliance (sandbox 24h
  session window; production sender + Business setup for a bought number).
- Losing Z-API means no instant fallback if Twilio is down.
- Media URLs are Twilio-hosted and time-limited; durable media storage is
  out of scope for this decision.

Rejected alternatives:
- Keep Z-API as a rollback route — unused after the number block, still
  required in config, doubles webhook surface.
- Store a JSON `provider_payload` instead of typed columns — harder to
  query, weaker schema, and we already know the Twilio form fields.
- Use `WaId` as the contact key — `From` is always present on Messaging
  webhooks and is what outbound `whatsapp:+{phone}` needs; `WaId` can be
  absent in edge cases.

See also: ADR 0005 (Cloudflare-proxied `api.<domain>` hostname — path is
now `/webhook/twilio`), task `020-twilio-sdk-only`.
