---
id: 036-chat-agent-classifier
feature: agent
status: done
---

# Classifier: chat vs agent

## Migration preflight

Before implementation, inspect the relevant sections of `docs/plan.md`, the governing ADRs, this task, and its directly dependent or consuming tasks. Record:

- target end-state and contracts introduced here;
- legacy code allowed only as a temporary rollback bridge;
- legacy imports, data paths, and behaviors forbidden in new code;
- the task that removes each temporary bridge;
- an architecture test or CI check that enforces the boundary.

## Scope
Add a tiny structured-output classifier (`chat` | `agent`) with tests.
No webhook wiring, no ack, no job-table change. 037 calls this. 038
adds the status line.

## Why
Every inbound today is an `agent_turn` (persona + tools). Small talk
should skip tools; tool work should stay on the worker. The classifier
is not an agent and not a WhatsApp speaker.

## Architecture (target; this task only builds the box)

```text
inbound (api, after 200)
  classifier { chat | agent }
    chat  → persona, tools=[]          # 037
    agent → ack → enqueue → run_turn   # 038 then worker
```

## Acceptance criteria
- [x] Module in `app/agent/` (e.g. `classify.py`): one Gemini Flash-Lite
      call (`GEMINI_CHAT_MODEL`), `thinking="minimal"`, **no tools**.
      Input = last user message + a few prior turns (reuse
      `load_recent_messages` shape: role/content). Output = structured
      `{ "intent": "chat" | "agent" }`.
- [x] Unsure, timeout, or parse failure → `agent` (never miss Gmail).
- [x] Bias the prompt: confirmations after a draft/tool turn (`sim`,
      `manda`, `pode enviar`) are `agent`. Small talk / jokes / “oi”
      are `chat`.
- [x] Tests mock `generate_content` (not a Pydantic AI agent): chat
      example, agent example, failure → `agent`. No live Gemini in CI.
- [x] Nothing in `app/api` or `app/worker` calls it yet.

## Out of scope
- Running the classifier from the webhook (037).
- `já tô nisso.` ack (038).
- Redis / a new queue / a third intent (`local` vs `heavy`).
- Flattening nested exec (`ask_execution` stays).

## Depends on
- ADR **0012** (persona/exec). This is a router in front, not a third
  Pydantic agent.

## Log
### [PA] 2026-08-14 16:05 — Grooming
Split from chat/agent architecture grill. Classifier is a JSON API
call. Fail-open to `agent`. History for “sim, manda”. Wiring is 037.

### [SWE] 2026-08-14 16:15 — Classifier module
`app/agent/classify.py`: Flash-Lite structured `{intent: chat|agent}`,
no tools, last 8 history rows, 5s timeout. Fail-open to `agent`.
Api/worker still do not import it. Tests pass (TestModel / FunctionModel).

### [PA] 2026-08-14 16:10 — ADR 0013
Spec: `docs/adr/0013-chat-on-api-agent-on-worker.md`. Classifier remains
unwired until 037.

### [SWE] 2026-08-14 16:20 — Drop Agent wrapper
ADR 0013: classifier is structured JSON, not an agent. Direct
`google.genai` `generate_content` (`thinking_level=MINIMAL`, JSON
schema, no tools). Tests mock the call.

### [SWE] 2026-08-14 16:22 — History in the classify prompt
Last user line is the target; prior turns are "Mensagens recentes
(contexto)" in the same `contents` string. No system_instruction, no
Gemini chat history.

### [SWE] 2026-08-14 16:25 — Ship
Commit, PR, merge to `main`.
### [PA] 2026-08-15 15:22 — Superseded architecture
ADR 0014 replaces classifier routing with one Interaction Agent. Keep
the shipped code until task 047 canaries, drains legacy jobs, and removes
the old path.
