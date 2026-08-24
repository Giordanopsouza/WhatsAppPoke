---
id: 028-whatsapp-audio-transcription
feature: whatsapp
status: pending
---

# WhatsApp audio transcription

## Migration preflight

Before implementation, inspect the relevant sections of `docs/plan.md`, the governing ADRs, this task, and its directly dependent or consuming tasks. Record:

- target end-state and contracts introduced here;
- legacy code allowed only as a temporary rollback bridge;
- legacy imports, data paths, and behaviors forbidden in new code;
- the task that removes each temporary bridge;
- an architecture test or CI check that enforces the boundary.

## Scope
When a contact sends a voice note (audio-only inbound), download the
Twilio media URL, transcribe it to text in the **worker**, persist the
transcription on the `message` row, and run a normal **agent turn** so
gg can reply as if the user had typed the message. Today audio is parsed
and stored (`media_url`, `media_content_type`) but skipped for turns
because `Body` is empty (ADR 0007).

## Acceptance criteria
- [ ] Inbound with `audio/*` media and empty (or whitespace-only) body
      enqueues an `agent_turn` (same coalesce rules as text).
- [ ] Worker resolves the triggering inbound row (via job payload
      `provider_message_id` or latest media-only inbound for the contact),
      downloads media from Twilio using account credentials, and
      transcribes before the LLM call.
- [ ] Transcription is written to `message.body` on that inbound row
      (Portuguese-friendly; no separate column in MVP).
- [ ] Transcription failure → one polite WhatsApp reply asking the user
      to retry or type instead; job completes without dead-letter for
      expected STT errors.
- [ ] History load includes the transcribed body; media-only rows no
      longer disappear from the LLM prompt after transcription.
- [ ] Tests: fixture `twilio_inbound_audio.json` → enqueue path;
      mocked Twilio media fetch + mocked STT; worker turn uses
      transcribed text. No live API keys in CI.
- [ ] Manual: send a Portuguese voice note on WhatsApp → gg replies
      to the spoken content within one turn.

## Out of scope
- Image / document / video understanding (separate task).
- Outbound voice or TTS replies.
- Durable object storage for media blobs (Twilio URLs stay ephemeral).
- Batching multiple voice notes into one turn.
- A dedicated `transcribe_audio` job kind unless enqueue-on-empty-body
  proves insufficient in implementation.

## Depends on
- **020** (done): Twilio inbound parse + typed media columns on
  `message`.

## Log
### [PA] 2026-08-12 20:10 — Grooming
Requested: WhatsApp audio recognition. Scoped to worker-side STT for
`audio/*` inbound, body backfill, then normal agent turn; vision/TTS
deferred.
