---
id: 011-write-tools-confirmation
feature: integrations
status: done
---

# Write tools with in-chat confirmation

## Scope
Mutating Google actions that affect **other people** (send email, invite
guests) are propose-then-confirm via `pending_action`. Solo calendar
events create immediately (reversible with delete). Confirmation always
arrives as a later WhatsApp turn — never framework HITL.

**Flows:**

| Action | Tool surface | On call | On confirm |
|---|---|---|---|
| Send email | `propose_send_email` | Create Gmail **draft**; store `pending_action` with `draft_id` + preview | `drafts.send` |
| Calendar, no guests | `create_event(..., attendees=[])` | `events.insert` now; return “created” + event id | — |
| Calendar, with guests | same `create_event` | Store fields in `pending_action` (no Google write); return preview | `events.insert` (sends invites) |
| Undo solo event | `delete_event` | `events.delete` by id | — |
| Confirm anything pending | `confirm_pending_action(kind)` | — | Contact's staged row of that `kind`, staged in an **earlier** turn |

One calendar write tool — implementation branches on `attendees`. Do
**not** expose `propose_create_event` separately (extra tool choice for
the model).

`pending_action` owns contact binding, 15-min expiry, and “nothing to
confirm”. Orphan Gmail drafts on ignore/cancel are acceptable (no
cleanup in this task).

## Acceptance criteria
- [x] Migration: `pending_action` (`contact_id` FK, `kind`, `payload`
      jsonb, `expires_at`, timestamps); RLS pattern per `28b0ac108edc`
- [x] OAuth: add `gmail.compose` (drafts create/send); keep
      `gmail.readonly` + `calendar.events`. Existing grants may need
      reconnect
- [x] `propose_send_email(to, subject, body)` creates a Gmail draft,
      stores `pending_action` with `draft_id` + preview, returns a
      human-readable preview the agent relays
- [x] Single `create_event(title, start, end, attendees=[])`:
      empty attendees → insert immediately and report created id;
      non-empty → stage `pending_action` only, return preview asking
      for confirmation (no invites until confirm)
- [x] `delete_event(id)` deletes a primary-calendar event (undo for
      solo creates)
- [x] `confirm_pending_action` executes the newest non-expired pending
      action (15-min expiry): email → `drafts.send`; guest event →
      `events.insert`. Expired → agent re-proposes
- [x] Ambiguous reply ("yes" with no pending action) → agent says
      there's nothing to confirm, does not invent one
- [x] Confirm must use the staged id/payload only — never re-infer
      to/subject/body/event fields from the chat turn
- [x] Write/confirm policy on tool definitions (docstrings); system prompt
      only keeps WhatsApp presentation + “don’t invent / reconnect”
- [x] Test/eval cases: no send / no guest-invite without an explicit
      confirmation turn; solo create does not require confirm
- [x] The separate-turn rule is enforced in SQL (`created_turn_id !=`
      current turn), not by tool docstrings: propose+confirm inside one
      model run cannot send
- [x] A claimed action survives a failed Google call (released back to
      `pending`) unless the outcome is unknown; DB failure on claim
      reports a temporary error, never “nothing pending”
- [x] `confirm_pending_action(kind)` only executes a staged action of
      that kind, and staging a proposal drops the contact's previous one

## Out of scope
- Multi-step wizards (edit a proposal before confirming)
- Bulk actions
- Deleting orphaned Gmail drafts on expiry/cancel
- Letting the user edit the draft in Gmail and re-syncing before send
- Outlook / other calendar providers

## Log
### [PA] 2026-08-05 15:45 — Grooming
Created from `docs/plan.md` Phase C. Depends on 009, 010.
### [PA] 2026-08-06 11:05 — Framework HITL approval evaluated, rejected
Pydantic AI's deferred-tools/human-in-the-loop approval targets
streaming UIs and resumable runs. Our approval arrives as a new
WhatsApp message minutes later, in a different job on possibly a
different worker — `pending_action` + `confirm_pending_action` is the
correct pattern for an async chat transport.
### [PA] 2026-08-08 20:47 — Gmail draft staging chosen
Email propose creates a real Gmail draft (Poke-style); confirm sends by
`draft_id`. Orphan drafts on ignore/cancel are fine for MVP.
### [PA] 2026-08-08 20:53 — Calendar: one tool, branch on attendees
No Calendar draft folder. Solo event → create immediately +
`delete_event` to undo. With guests → internal `pending_action` until
confirm (avoids firing invites). Single `create_event` tool; do not
split into propose/create for the model.
### [AI] 2026-08-08 21:10 — Implemented
Migration + ORM + DB claim helpers; OAuth `gmail.compose`; Google draft
create/send + calendar insert/delete; tools registered; system prompt
write rules; unit tests for propose/confirm/solo paths.
### [AI] 2026-08-08 21:12 — System prompt slimmed
Propose/confirm policy lives on tool docstrings only; system prompt keeps
presentation + don’t-invent/reconnect.
### [AI] 2026-08-08 22:40 — Code review fixes: confirmation gate hardened
Review found the confirmation was enforced only by a docstring. Three
fixes, migration `e5f9c3d20b16`:

1. **Separate turn required.** `AgentDeps.turn_id` (the job id) is stamped
   on the staged row as `created_turn_id`; the claim filters
   `created_turn_id IS DISTINCT FROM :turn_id`. Propose+confirm inside one
   model run now claims nothing.
2. **Claim, don't delete.** `UPDATE … SET status='claimed' … RETURNING`
   replaces `DELETE … RETURNING`; the row is discarded only after Google
   succeeds, released back to `pending` when Google answered with an error
   (nothing written), and consumed when the outcome is unknown (transport
   error / unparseable response) so a retry cannot double-send. Claim
   failing on a DB error returns `CONFIRM_UNAVAILABLE`, not
   “nothing pending”.
3. **Confirm by kind, one slot.** `confirm_pending_action(kind)` claims
   only a matching row, and staging a proposal deletes the contact's
   previous one — an abandoned proposal can no longer be fired by a
   stray “ok”, and confirming an email can't create an event.

Confirming by row **id** was rejected: `history_to_prompt_and_messages`
replays only user/assistant text, so a staged id from an earlier turn is
not in the model's context — it would have to be pasted into the person's
WhatsApp message. `kind` + one-slot gives the same protection with no UX
cost.

Still open (from the same review, not in this pass): all-day `end.date`
is exclusive and unnormalized; `delete_event` reports success on a 404;
`format_events` never exposes `event_id`; `to` isn't validated as an
address; `claim_pending_action` has no Postgres-backed test (the new test
asserts the compiled statement instead).
### [AI] 2026-08-09 — Done
Merged PR #12; moved to `tasks/done/`.
