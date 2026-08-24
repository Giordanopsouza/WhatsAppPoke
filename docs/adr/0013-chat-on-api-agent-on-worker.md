0013. Chat on api, agent on worker
Status: Superseded by ADR 0014
Date: 2026-08-14

Context
Every inbound is an `agent_turn` job: persona (local tools +
`ask_execution`) then one WhatsApp reply. Small talk pays the tool
loop and the queue claim. Tool turns (Gmail, briefing) sit in silence
until exec finishes (LLM p95 ~21s).

Poke-style advice was: cheap classifier, status line, queue the slow
path. We already have the queue (Postgres `job`, ADR 0002). We do not
need Redis/Rabbit/SQS. We also already have persona + exec (ADR 0012)
for MCP cost — that is not a third WhatsApp route.

The user-visible reply is Twilio `send_text`, not the webhook 200.
Holding Gemini inside the Twilio request does not make WhatsApp
faster; it risks retries. Task 002 moved turns off `asyncio.create_task`
onto jobs for durability. Chat can give that up; tool work cannot.

Decision
Two paths only. A classifier (structured JSON, not an agent) picks
`chat` or `agent`. Nested exec stays inside the agent (ADR 0012). No
`local` vs `heavy` third route.

1. **Webhook** still verifies, persists inbound, returns 200. Duplicate
   `provider_message_id` still drops. Empty body still no LLM.
2. **After 200**, the **api** runs classifier + chat in-process (no
   `job`). Fail or timeout → `agent`. Small talk after a tool turn is
   still `chat`; only this message's tool need (or a short confirmation)
   is `agent`. Classifier sees the last user line plus a few prior turns
   (acks stripped). Same `GEMINI_CHAT_MODEL`, `thinking="minimal"`, no
   tools.
3. **Chat**: persona, same `system_prompt.md`, `tools=[]`. One
   `send_text`. Not a job. No advisory lock on the api (double-reply
   on overlap is accepted). Process death after 200 → no retry. A live
   Gemini error still sends `FALLBACK_REPLY`.
4. **Agent**: api sends hardcoded `já tô nisso.` (persist), then
   enqueues `agent_turn` (retry insert once). Twilio rejecting the ack
   still enqueues. Worker runs today’s `run_turn` and sends the real
   reply. Retry: last outbound is the ack constant → still run;
   last outbound is a real reply → skip.
5. **Short circuits on the api, before classify**: briefing STOP /
   opt-in-only stay fixed replies (ADR 0010). `briefing_cue` skips
   classify and takes the agent path (ack + enqueue).
6. **Worker** still owns tool turns, reminders, connect notify,
   outbound sweep. Per-contact advisory lock (ADR 0002) still applies
   to worker jobs, not to api chat.

This revises the AGENTS.md rule “api must not run LLM” for classifier
and chat only. Tools stay off the webhook and off the api.

Consequences
Positive: “oi” skips the queue and tool schemas. Tool waits get a
status bubble from the api before claim. One durable queue still
serializes agent work. Persona/exec split unchanged.

Negative: chat is fire-and-forget (no job, no lock). Api chat can
overlap a worker agent for the same contact. Ack-then-enqueue can
leave a status line with no job if both insert retries fail. Two
WhatsApp messages (and two Twilio sends) on the agent path. Gemini
now runs on the api service.

Rejected alternatives:
- Redis / BullMQ / SQS — second broker; `job` already is the fila.
- Three intents (`chat` | `local` | `heavy`) — a third agent in all
  but name; tasks and Gmail both go to the agent.
- Classifier or chat inside the Twilio request — slower 200, no faster
  user bubble.
- Ack from the worker — status waits on claim again.
- Token streaming — no WhatsApp wire for partial tokens.
- Flattening exec onto persona — re-pays MCP schemas on tool turns
  that should stay nested (ADR 0012).

See also: ADR 0002 (queue + worker lock), ADR 0003 (Gemini Flash),
ADR 0010 (briefing knock), ADR 0011 (reminder ping), ADR 0012
(persona/exec), tasks `036-chat-agent-classifier`,
`037-api-chat-path`, `038-agent-ack`.
