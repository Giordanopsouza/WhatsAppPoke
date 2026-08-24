---
id: 015-evals-harness
feature: evals
status: pending
---

# Golden-set evaluation harness (Pydantic Evals)

## Migration preflight

Before implementation, inspect the governing ADRs, this task, and its directly dependent or consuming tasks. Record:

- target end-state and contracts introduced here;
- legacy code allowed only as a temporary rollback bridge;
- legacy imports, data paths, and behaviors forbidden in new code;
- the task that removes each temporary bridge;
- an architecture test or CI check that enforces the boundary.

## Scope
`evals/` built on **Pydantic Evals** (ships inside `pydantic-ai`):
`Dataset`/`Case` definitions in YAML plus deterministic code evaluators.
Two execution modes: offline against `TestModel`/`FunctionModel` (free,
deterministic — tool-selection and flow cases) and live against the
configured Gemini Interaction/Execution models (quality cases). Runs on
every prompt/model/tool change — never in the request path.

## Acceptance criteria
- [ ] `evals/` dataset(s) in YAML loaded via `Dataset.from_file`;
      each case: inputs `(history, user_message)`, expected tool calls
      (name + args match), expected reply properties (must-contain /
      regex / max-length)
- [ ] Deterministic evaluators as code assertions; tool-call cases run
      against `FunctionModel`/`TestModel` with no LLM spend
- [ ] Live mode runs the same dataset against the configured Gemini models;
      `EvaluationReport` printed and written as JSON with model/prompt
      identifiers
- [ ] Seed cases cover Interaction orchestration
      (`send_message_to_user`, wait, dispatch, five-message fuse),
      detached result re-entry, every owned/local Execution tool, active
      execution context, reminder vs Automation, and integration request
- [ ] Adversarial cases cover prompt injection in email/event content,
      same-turn and ambiguous confirmation, duplicate result event,
      disconnected toolkit, cross-tenant phrasing, stale execution
      result, and injection through memory content
- [ ] Deterministic security evaluators fail any direct sensitive write
      without a valid claimed `pending_action`, any Execution outbound,
      any generic proxy call, or any tool attached for a disconnected app
- [ ] `uv run python -m evals.run [--live]` — offline by default,
      exits non-zero on failure so it can gate a release manually
- [ ] `evals/README.md`: when to run (any prompt/model/tool change or
      harness/framework upgrade) and how to read the report

## Out of scope
- LLM-as-judge as the only release gate
- Logfire score tracking over time (post-launch)

## Log
### [PA] 2026-08-05 15:45 — Grooming
Created from Phase E. Depends on 004; cases grow as
tools land (009–014).
### [PA] 2026-08-06 11:05 — Rebuilt on Pydantic Evals
Framework audit: `pydantic-ai` ships a code-first eval library
(Dataset/Case/evaluators/EvaluationReport, YAML datasets, Logfire
visualization) plus `TestModel`/`FunctionModel` for offline runs.
Replaces the hand-rolled runner/graders/report previously specced —
same golden-set philosophy, maintained machinery.
### [PA] 2026-08-15 15:22 — New runtime release gate
Rebase datasets on ADRs 0014–0016. Task 047 cannot cut over without the
offline orchestration, idempotency, and sensitive-action cases passing.
