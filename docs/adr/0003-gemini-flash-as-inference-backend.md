0003. Gemini Flash as the inference backend
Status: Superseded by ADR 0020
Date: 2026-08-07

Context
The MVP originally used Fireworks (OpenAI-compatible) via Pydantic AI.
WhatsApp turns need low latency and short replies; we also want a
simple provider path (API key + model name) without maintaining a
custom OpenAI-compatible base URL for the chat path.

Decision
Use Google Gemini Flash models through Pydantic AI's `GoogleModel` /
`GoogleProvider`.

- Chat model comes from `GEMINI_CHAT_MODEL` (e.g. `gemini-3.5-flash-lite`);
  exec/MCP model from `GEMINI_EXEC_MODEL` (e.g. `gemini-3.7-flash`). See
  ADR 0012. (`GEMINI_MODEL` was the single-agent predecessor.)
- Prefer Flash / Flash-Lite over Pro for chat latency and cost.
- Persona (WhatsApp): `thinking="minimal"`. Exec (Composio):
  `thinking="low"`. Unified `thinking` only — do not hardcode
  `thinking_budget` (Gemini 3.5 rejects it). See ADR 0012.

Fireworks remains a past option; the agent path is Gemini unless we
revisit this ADR.

Consequences
Positive:
- Lower setup friction (Google AI Studio key + model env).
- Flash models fit short WhatsApp turns; persona thinking stays minimal.
- Model swaps stay config-only via `GEMINI_CHAT_MODEL` /
  `GEMINI_EXEC_MODEL` (ADR 0012).

Negative / tradeoffs:
- Tied to Google's thinking APIs (budget vs level) when changing
  generations — use unified `thinking`, not raw Google fields.
- Docs/tasks that still say Fireworks for the agent are stale until
  updated.
