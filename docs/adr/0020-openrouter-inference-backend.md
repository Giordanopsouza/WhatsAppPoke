0020. OpenRouter as the inference backend
Status: Accepted
Date: 2026-08-18

Context
ADR 0003 chose Google Gemini Flash through Pydantic AI's `GoogleModel` /
`GoogleProvider` (Google AI Studio key). We still want Flash-class models
for WhatsApp latency and cost, but we want one API key that can route to
any OpenRouter slug without a custom OpenAI-compatible base URL.

Pydantic AI documents a first-class OpenRouter path: install
`pydantic-ai-slim[openrouter]`, then `OpenRouterModel` +
`OpenRouterProvider`, or the `openrouter:` model-string prefix. Settings
use `OpenRouterModelSettings` (`openrouter_reasoning`, not Google
`thinking`).

Decision
Route all agent inference through OpenRouter.

- API key is `OPENROUTER_API_KEY` (https://openrouter.ai/keys).
- Interaction uses `OPENROUTER_CHAT_MODEL` (e.g.
  `google/gemini-3.5-flash-lite`); Execution uses
  `OPENROUTER_EXEC_MODEL` (e.g. `google/gemini-3.6-flash`). Slugs are
  OpenRouter `provider/model` ids, not Google AI Studio names.
- Construct `OpenRouterModel` with `OpenRouterProvider` so the key is
  explicit (same pattern as ADR 0003). Pass `app_url` / `app_title` for
  OpenRouter app attribution.
- Persona (WhatsApp): `openrouter_reasoning={"effort": "minimal"}` and
  `max_tokens=3000`. Do not use `GoogleModelSettings.thinking`.
- Model swaps stay config-only via the two OpenRouter env vars.

`GOOGLE_API_KEY`, `GEMINI_CHAT_MODEL`, and `GEMINI_EXEC_MODEL` are
removed.

Consequences
Positive:
- One key, any OpenRouter slug (Gemini, Anthropic, OpenAI, …) without
  changing provider code.
- Same dual-model split as ADR 0012 / 0014; Flash-class defaults stay.

Negative / tradeoffs:
- Adds OpenRouter as a hop (routing, extra vendor). Reasoning flags
  follow OpenRouter's `reasoning.effort`, not Google's native thinking
  API.
- Production must set the new vars before deploy; leftover Google AI
  Studio vars are ignored.

See also: ADR 0003 (superseded), ADR 0012 (dual models), ADR 0014
(Interaction + Execution).
