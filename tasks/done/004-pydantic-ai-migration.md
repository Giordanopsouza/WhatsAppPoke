---
id: 004-pydantic-ai-migration
feature: agent
status: done
---

# Migrate agent loop to Pydantic AI

## Scope
Replace the hand-rolled tool loop in `app/agent/loop.py` with a
Pydantic AI `Agent` running on Fireworks' OpenAI-compatible endpoint.
Tools become typed functions with pydantic validation; per-turn context
(contact, db session factory) injected via `RunContext` deps.

## Acceptance criteria
- [x] `uv add "pydantic-ai-slim[openai,logfire]"` (drop the raw `openai`
      client dependency if nothing else uses it)
- [x] Model configured via Fireworks base URL + `FIREWORKS_MODEL`;
      system prompt and short-reply behavior preserved
- [x] Existing `tavily_search` re-expressed as an async typed tool
      (`httpx.AsyncClient`, hard timeout ≤ 8s)
- [x] `UsageLimits(request_limit=6)` caps tool-call iterations — an
      infinite tool loop is impossible
- [x] Contact-scoped deps via `RunContext`, no globals for per-turn state
- [x] DB history → `message_history`: map `message` rows to
      `ModelRequest(UserPromptPart)` / `ModelResponse(TextPart)` (tool
      exchanges from past turns are not reconstructed — accepted)
- [x] `ReinjectSystemPrompt` capability (or equivalent) enabled —
      Pydantic AI skips the configured system prompt when
      `message_history` is non-empty, and our DB history never contains
      one. Verify instructions actually apply on history-bearing turns
- [x] Unit tests run on the built-in `'test'` model / `FunctionModel`
      — no Fireworks key needed, no spend, deterministic
- [ ] Smoke test: multi-turn conversation still remembers earlier turns;
      fallback line still sent when the LLM call raises

## Out of scope
- New tools (calendar/gmail/tasks/memory — later tasks)
- Logfire dashboards (task 005)
- Structured output / response schemas

## Log
### [PA] 2026-08-05 15:45 — Grooming
Created from `docs/plan.md` Phase A. Decision rationale in plan.md —
LangGraph's durability duplicates our queue; Pydantic AI is the
async-native fit for this stack.

### [SWE] 2026-08-06 14:05 — Start
Implementing Pydantic AI migration: deps, async tavily tool, history
mapping, ReinjectSystemPrompt, UsageLimits, unit tests.

### [SWE] 2026-08-06 14:20 — Complete
Replaced hand-rolled OpenAI loop with `pydantic-ai-slim` Agent on
FireworksProvider. Worker calls async `run_turn` with `AgentDeps`.
Five unit tests pass on TestModel/FunctionModel. Live multi-turn +
fallback smoke left for Tester.
