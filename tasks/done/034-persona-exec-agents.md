---
id: 034-persona-exec-agents
feature: agent
status: done
---

# Persona + execution agents (Poke-style split)

## Scope
Split the single WhatsApp agent turn into two Pydantic-AI agents:
**persona** (talks to the user; local tools only) and **tool/exec**
(Composio MCP; returns facts to persona). Small talk never attaches MCP.
Wire persona → exec via one tool (`ask_execution`). Two Gemini model
env vars. Reminder fire stays deterministic (033 / ADR 0011) — not a
third agent.

## Why
ADR 0009 already accepted this as the **later** direction after the
enable allowlist: connected-contact `"hey"` still pays allowlisted
schemas on every turn until MCP lives only on the subagent. Matches the
Poke personality / execution split in
`docs/reference/prompts/poke_system_prompt_inspiration.md`.

## Architecture (target)

```text
Usuário WhatsApp
      ↕
 persona ──ask_execution(goal)──► tool (Composio MCP allowlist)
      │
      └─ local: tasks, reminders CRUD, tavily, request_integration
```

| | **persona** | **tool** (exec) |
|---|---|---|
| Speaks to user? | Yes | No — facts back to persona |
| Model | `GEMINI_CHAT_MODEL` (e.g. `gemini-3.5-flash-lite`) | `GEMINI_EXEC_MODEL` (e.g. `gemini-3.7-flash`) |
| Static tools | locals + `ask_execution` | `[]` |
| Toolsets on `run` | none | Composio MCP (029 allowlist) |
| System prompt | `system_prompt.md` + delegate rules | short execution prompt (no WhatsApp humor) |
| Dynamic injectors | clock, integrations, briefing | clock (+ connected toolkits) |
| Public API | `run_turn` | only via `ask_execution` |

## Acceptance criteria
- [x] `Settings`: `GEMINI_CHAT_MODEL` + `GEMINI_EXEC_MODEL` (migrate
      from single `GEMINI_MODEL`; update `.env.example` + `docs/deploy.md`).
      Shared `_build_model(model_name: str)` helper.
- [x] Two agents in `app/agent/loop.py` (names e.g. `persona` /
      `tool`): persona has `LOCAL_TOOLS` + `ask_execution`; tool has no
      static tools and receives MCP only when `ask_execution` runs it.
- [x] `run_turn` runs **persona** only; does **not** attach Composio
      MCP toolsets to the persona `agent.run`.
- [x] `ask_execution(ctx, goal: str) -> str` runs the tool agent with
      `deps` / `usage` from the parent, builds MCP via existing
      `load_turn_integrations` + `build_turn_toolsets`, returns factual
      text (errors as plain text the persona can relay).
- [x] Persona system prompt (static and/or injector): SaaS work
      (Gmail / Agenda / Notion / …) goes through `ask_execution`; never
      name the subagent to the user; small talk needs no tool.
- [x] New short **execution** system prompt file (or constant): complete
      the goal; output for the persona not WhatsApp; no gg banter.
- [x] Briefing cue / connected-integrations injectors stay on **persona**
      (briefing may call `ask_execution` when sources are connected).
- [x] Usage limits: parent turn still bounded; child run shares
      `usage=ctx.usage` so totals aggregate (Pydantic AI multi-agent
      pattern).
- [x] Tests: persona `"hey"` / no MCP attach; `ask_execution` mocked or
      TestModel path proves tool agent gets toolsets and persona does
      not; existing local-tool tests still pass. No live Gemini/Composio
      in CI.
- [x] ADR: promote ADR 0009 “later” split to done **or** add a short
      ADR (e.g. 0012) for persona/exec + dual model env; update
      `docs/glossary.md` if “agent turn” wording assumes one model call.
- [ ] Manual: connected contact `"oi"` stays cheap (no MCP on persona);
      “o que tem no inbox?” → persona delegates → WhatsApp reply in gg
      tone with real mail summary.

## Out of scope
- Reintroducing a reminder LLM agent (033 / ADR 0011 stands).
- Changing the Composio allowlist slugs (029).
- Deferred tool loading / meta-tool search (ADR 0009 option 2).
- Parallel `ask_execution` fan-out or multi-exec sessions.
- Streaming / interim “tô olhando…” WhatsApp messages (product follow-up).
- Moving local tasks/reminders/Tavily onto the exec agent.

## Depends on
- **029** (allowlist): exec MCP surface must be enable-only, not the
  full Direct Tools catalog.
- **033** / ADR **0011**: reminder path is not part of this split.
- ADR **0009**: documents the intended chat/exec split.

## Log
### [PA] 2026-08-13 21:20 — Grooming
Scoped persona + tool/exec two-agent split with `ask_execution`, dual
`GEMINI_*_MODEL`, MCP only on exec. Reminder stays deterministic.
Depends on 029 allowlist. Acceptance includes ADR/glossary and tests
that persona never attaches MCP.

### [SWE] 2026-08-13 21:40 — Implementation
Split `agent_persona` / `agent_tool` with `ask_execution`, dual
`GEMINI_CHAT_MODEL` + `GEMINI_EXEC_MODEL`, MCP only on exec. ADR 0012.
Persona `"hey"` does not call `build_turn_toolsets`.

### [SWE] 2026-08-13 21:47 — Done
Shipped persona/exec split, dual Gemini env, thinking minimal vs medium.
Moved to `tasks/done/`.
