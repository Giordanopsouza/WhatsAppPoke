0011. Deterministic reminder ping (no LLM at fire)
Status: Accepted
Date: 2026-08-13

Context
Task 013 composed the WhatsApp ping with a short tool-less agent turn so
the outbound had “gg’s voice.” The reminder row already stores the full
`body` at `set_reminder` time. That extra Gemini call added latency and
cost for a message that does not need to be rewritten. Briefing knocks
(ADR 0010) already send without an LLM.

Decision
`handle_reminder_due` formats `reminder.body` with a pure helper
(`format_reminder_ping`) and sends that text. No history load, no
`reminder_agent`. In-window still uses `send_text`; out-of-window still
uses the Utility Content Template (`TWILIO_REMINDER_CONTENT_SID`) with
`body_variable` set to the same deterministic string.

Consequences
Positive: reminder fire is as cheap as a briefing knock; ping copy is
exactly what was scheduled. Negative: the ping is not rewritten in gg’s
tone. Claim / release / persist / cancelled-noop behaviour is unchanged.
The Utility template copy and ContentSid are unchanged.
