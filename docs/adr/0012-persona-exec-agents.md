0012. Persona + execution agents (dual Gemini)
Status: Superseded by ADR 0014
Date: 2026-08-13

Context
ADR 0009 shipped an enable allowlist so connected-contact turns stopped
paying ~150k tokens of Composio schemas. The allowlisted surface still
attached to the WhatsApp-facing agent, so `"oi"` from a connected
contact paid those schemas on every turn. Option 3 in 0009 was the
accepted later direction: chat agent vs execution subagent.

Reminder fire is already deterministic (ADR 0011) — not a third agent.

Decision
Split the WhatsApp turn into two Pydantic AI agents:

1. **persona** (`agent_persona`, `GEMINI_CHAT_MODEL`, e.g.
   `gemini-3.5-flash-lite`, `thinking="minimal"`): speaks on WhatsApp;
   local tools plus `ask_execution`; never attaches Composio MCP.
2. **exec / tool** (`agent_tool`, `GEMINI_EXEC_MODEL`, e.g.
   `gemini-3.7-flash`, `thinking="low"`): Composio MCP allowlist
   only (ADR 0009); returns facts to persona; no WhatsApp voice.

`run_turn` runs persona only. `ask_execution` loads integrations, builds
MCP via the existing session helper, and runs the exec agent with
`usage=ctx.usage` so parent `UsageLimits` aggregate across both runs.

`GEMINI_MODEL` is replaced by the two vars on **api and worker**.

Consequences
Positive: small talk on a connected contact stays cheap; personality and
tool-calling can use different Flash SKUs; the allowlist still gates
exec.

Negative: a SaaS question is two model runs (added latency); we own two
env vars. Not chosen: deferred loading / meta-tool search (0009 option
2); parallel `ask_execution`; a reminder LLM (0011 stands).

See also: ADR 0003 (Gemini Flash), ADR 0008 (Composio-first), ADR 0009
(allowlist; this ADR ships the later split), ADR 0011 (deterministic
reminder), tasks `029-composio-tool-allowlist`,
`034-persona-exec-agents`.
