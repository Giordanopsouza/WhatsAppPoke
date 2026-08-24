0010. Daily briefing knock via worker-seeded sweep
Status: Superseded by ADR 0017
Date: 2026-08-13

Context
The product wants a weekday morning WhatsApp ping that leads into a
Gmail/Calendar briefing (or a Gmail connect link). Options on the table:
pg_cron / a fourth Railway process; compose the briefing at 08:00 and
stuff it into a template variable; opt-in before the first knock; a
São Paulo 07:40 wall-clock tick.

A fourth process and pg_cron add operational surface we already rejected
for reminders (Postgres `job` is the queue). Composing at send time
cannot carry a connect URL (10-minute `connect_link` TTL) and cannot
run MCP tools inside a Marketing template. A São Paulo 07:40 tick
misses contacts whose `tz` is not America/Sao_Paulo.

Decision
1. **Knock always, compose after reply.** Weekday 08:00 local is an
   approved pt-BR `twilio/quick-reply` Marketing template (button
   **Ver agora**). No LLM, no connect URL, no `{{1}}` body. In-window
   and out-of-window both use `ContentSid`. After any inbound that
   finds `briefing_state.pending_at` set, the existing `agent_turn`
   path injects a cue: Gmail and/or Calendar connected → short briefing
   via MCP tools; neither → `request_integration` for **gmail** only.
2. **Same queue, no cron.** Worker boot inserts one pending
   `outbound_sweep` if none is pending or running (`run_at = now()`).
   The handler fans out per-contact `outbound_due` jobs, then inserts
   the next sweep at **now + 15 minutes**. Replica safety is the
   partial unique index (at most one pending sweep) plus
   `FOR UPDATE SKIP LOCKED` plus `IntegrityError` on insert. Fifteen
   minutes (not 07:40 São Paulo) is the tick so every `contact.tz`
   still hits local 08:00.
3. **Cadence and catch-up.** Default daily, weekends off in `contact.tz`.
   Three consecutive knocks with no consuming inbound → `weekly`
   (~7 days, still skip Sat/Sun). Any inbound that consumes the pending
   knock resets to daily. If `outbound_due` is claimed at local hour
   ≥ 12, skip the send, stamp `last_knock_on` (no unanswered bump), and
   let the next sweep schedule the next weekday 08:00. US Marketing
   error 63049 is logged and skipped (no retry).
4. **Opt-out is briefing-only.** `STOP` / “para” / “parar o briefing”
   sets `opted_out_at` and does not inject a briefing cue. Reminders
   are unchanged. Re-opt-in (“volta o briefing”) is cheap and included.

`job.contact_id` is nullable only for `outbound_sweep`; every other
kind still requires a contact (check constraint).

Consequences
Positive:
- No new process, broker, or cron. Reminders and briefing share the
  worker claim loop.
- Template knocks work outside the 24h customer-service window.
- Connect links are minted at reply time, not at 08:00.

Negative / tradeoffs:
- Knock time is ±15 minutes of local 08:00, not exact.
- A late worker (after local noon) skips that morning rather than
  sending a stale knock.
- Auto-send to every contact (no opt-in) depends on Marketing-template
  approval in Brazil; US 63049 is accepted as “do not deliver.”

Rejected alternatives:
- pg_cron / Redis / a fourth Railway service — extra infra for a
  15-minute tick the worker already can own.
- Compose at 08:00 into `{{1}}` — connect TTL, MCP, and Marketing
  variable rules all fight this.
- São Paulo 07:40 cron — wrong for non-SP timezones.
- Utility category — Meta will not accept an auto-send briefing as
  Utility; Marketing is required.
