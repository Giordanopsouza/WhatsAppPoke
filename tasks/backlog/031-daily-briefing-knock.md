---
id: 031-daily-briefing-knock
feature: outbound
status: in_progress
---

# Daily briefing knock

## Migration preflight

Before implementation, inspect the relevant sections of `docs/plan.md`, the governing ADRs, this task, and its directly dependent or consuming tasks. Record:

- target end-state and contracts introduced here;
- legacy code allowed only as a temporary rollback bridge;
- legacy imports, data paths, and behaviors forbidden in new code;
- the task that removes each temporary bridge;
- an architecture test or CI check that enforces the boundary.

## Scope
Auto-send a weekday morning WhatsApp knock to every contact (no
opt-in). Copy is a pt-BR quick-reply template (“briefing pronto” +
**Ver agora**). After they reply, run a normal **agent turn**: briefing
if Gmail **or** Calendar is connected, otherwise a Gmail connect link.
No cron — worker-seeded `outbound_sweep` fans out per-contact
`outbound_due` jobs, same queue as reminders.

## Product rules (locked)

- **Who:** every `contact`, weekends off in `contact.tz`. Default
  08:00 local.
- **Knock always, compose after reply.** The 08:00 message is the
  template (works inside and outside the 24h window; the button needs
  ContentSid). Do not compose Gmail/Calendar until inbound consumes the
  knock. Do not put a connect URL in the knock (`connect_link` TTL is
  10 min).
- **Follow-up:** Gmail or Calendar active → agent turn with tools
  (gg’s voice). Neither → `request_integration` for **gmail** only.
  Either integration is enough to call it a briefing.
- **Backoff:** 3 consecutive knocks with no consuming inbound →
  cadence `weekly` (≈7 days, still skip Sat/Sun). Any inbound that
  consumes the pending knock resets cadence to `daily`.
- **Catch-up:** if the worker claims an `outbound_due` after 12:00
  local, skip the send and schedule the next weekday 08:00. Before
  12:00, send.
- **Opt-out** is briefing-only (not reminders): `STOP`, “para”, “parar
  o briefing”. Store on `briefing_state`; honor before send.
- **v1 is this daily knock only** — no important-email watch.

## Acceptance criteria

- [x] ADR `docs/adr/0010-daily-briefing-knock.md` + glossary rows
      (`briefing knock`, `outbound_sweep`, cadence) + `docs/database.md`
      ER in the **same PR**.
- [x] Migration (RLS + revoked Data API grants, `contact_id` tenant
      pattern except the sweep job):
      - `job.kind` adds `outbound_sweep` | `outbound_due`.
      - `job.contact_id` **nullable**; check: `outbound_sweep` ⇒
        `contact_id IS NULL`, every other kind ⇒ `NOT NULL`.
      - Partial unique: at most one **pending** `outbound_sweep`.
      - Partial unique: at most one **pending** `outbound_due` per
        contact.
      - `briefing_state` (`contact_id` PK/FK, `opted_out_at` null,
        `pending_at` null, `unanswered_knocks` int default 0,
        `cadence` check in (`daily`, `weekly`) default `daily`,
        `last_knock_on` date null, timestamps).
- [x] Worker boot: if no pending `outbound_sweep`, insert one
      (`run_at = now()`). Handler is idempotent under two replicas
      (unique index + `SKIP LOCKED`).
- [x] `outbound_sweep` handler: for each contact not opted out, with
      no pending `outbound_due`, whose next weekday 08:00 (respecting
      cadence + weekends) is due, insert `outbound_due`. Then insert
      the **next** `outbound_sweep` (`run_at` ≈ 15 min, or next 07:40
      before São Paulo 08:00 — pick one, document it) and complete.
- [x] `outbound_due` handler: `contact_turn_lock`; skip if opted out,
      weekend, already knocked this local date, or local time ≥ 12:00;
      else send the approved quick-reply template via `twilio_wa`
      (`ContentSid`, no `Body`); persist outbound `message`; set
      `pending_at`, bump `unanswered_knocks`, flip cadence to `weekly`
      when the bump reaches 3; complete. No LLM on this path.
- [x] Inbound while `pending_at` is set: normal `agent_turn` (existing
      webhook enqueue). Worker clears `pending_at`, resets
      `unanswered_knocks` + cadence `daily`, and injects a cue so the
      turn **delivers the briefing or Gmail connect** and still answers
      any extra user text in the same turn (“ver agora, cancela a
      reunião”).
- [x] Cue rules: connected `gmail` and/or `googlecalendar` → read
      today’s mail/agenda (MCP tools already on the turn) and write a
      short WhatsApp briefing; neither → one Gmail connect link via
      `request_integration` (mint at send time). Empty inbox/agenda →
      still a short “nada urgente hoje”, not silence.
- [x] Twilio: pt-BR `twilio/quick-reply` template, category
      **MARKETING** (auto-send; Meta will not accept Utility). Body
      must not start/end with `{{1}}`. Button title `Ver agora` (≤20
      chars). Env `TWILIO_BRIEFING_CONTENT_SID` on api **and** worker;
      document create + submit + SID in `.env.example` +
      `docs/deploy.md`. Extend `send_content_template` if the knock
      needs more than a single `{{1}}`.
- [x] Opt-out inbound (`STOP` / `parar` / `para o briefing`,
      case-insensitive) sets `opted_out_at`, does not enqueue a
      briefing cue, and does not send a knock afterwards. Reminders
      unchanged. Re-opt-in can be a later chat (“volta o briefing”) if
      cheap; otherwise out of scope and noted.
- [x] Tests (mocked Twilio, no live keys): sweep fans out; weekend
      skip; post-12:00 skip; three unanswered knocks → weekly;
      inbound consumes pending + resets cadence; no-integration cue
      asks Gmail; in-window vs out-of-window both use the template for
      the knock; replica-safe sweep seed.
- [ ] Manual: weekday 08:00 knock with **Ver agora** → tap → briefing
      or Gmail link; ignore 3 mornings → next knock ~1 week later;
      Sat/Sun silent.

## Out of scope

- Important-email / calendar-event pushes (later task).
- User-chosen briefing hour (08:00 local is the v1 default).
- Changing reminder Utility-template behaviour.
- US Marketing delivery (63049) — Brazil-first; do not retry 63049.
- Recurring reminders, snooze, or stuffing the briefing into `{{1}}`.
- A fourth Railway process or pg_cron / Redis.

## Depends on

- **002** (done): worker claim loop + `contact_turn_lock`.
- **013** (done): delayed jobs, 24h helpers, `send_content_template`.
- **023** / **024** (done): `request_integration` + Composio MCP tools.

## Log
### [PA] 2026-08-13 14:41 — Grooming
Architecture pass: auto daily knock (no opt-in), worker sweep not
cron, compose after reply, Gmail-or-Calendar, Gmail-first CTA, pt-BR
quick-reply, weekends off, 3 missed knocks → weekly. v1 daily only.
Locked remaining defaults in this file (always-template knock, 08:00 /
skip after 12:00, any inbound consumes pending, briefing-only opt-out).

### [dev] 2026-08-13 15:30 — Implementation
Worker-seeded 15-min `outbound_sweep` (not SP 07:40), `briefing_state`,
nullable `job.contact_id` for sweep only, always-template knock,
compose-after-reply cue, briefing-only opt-out + cheap re-opt-in.
