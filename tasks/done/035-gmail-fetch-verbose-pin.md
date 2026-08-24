---
id: 035-gmail-fetch-verbose-pin
feature: agent
status: done
---

# Pin Gmail list verbose + lower exec thinking

## Migration preflight

Before implementation, inspect the relevant sections of `docs/plan.md`, the governing ADRs, this task, and its directly dependent or consuming tasks. Record:

- target end-state and contracts introduced here;
- legacy code allowed only as a temporary rollback bridge;
- legacy imports, data paths, and behaviors forbidden in new code;
- the task that removes each temporary bridge;
- an architecture test or CI check that enforces the boundary.

## Scope
Force `GMAIL_FETCH_EMAILS` to `verbose: false` in the MCP client so
full HTML bodies never re-enter the exec model. Drop exec thinking
from `medium` to `low`.

## Acceptance criteria
- [x] `process_tool_call` on the Composio `MCPToolset` overwrites
      `verbose` to `false` for `GMAIL_FETCH_EMAILS` (other tools
      unchanged; caller dict not mutated).
- [x] Unit tests cover the pin and `build_mcp_toolset` wiring.
- [x] Exec `GoogleModelSettings.thinking` is `"low"` (persona stays
      `"minimal"`). ADRs 0003 / 0012 match.

## Out of scope
- Result truncation for other MCP tools.
- Schema-hiding `verbose` from the tool definition.
- Prompt-only instructions as the control.

## Log
### [SWE] 2026-08-14 10:22 — Start
Logfire trace `01a00087e80468b06022e44ce7666246`: exec Gemini spent
44s on 701k tokens after `GMAIL_FETCH_EMAILS` with `verbose: true`.

### [SWE] 2026-08-14 10:25 — Pin + thinking=low
`pin_gmail_fetch_args` on MCPToolset; exec thinking `low`; tests pass.

### [SWE] 2026-08-14 10:26 — Ship
Commit, PR, merge to `main` for Railway.
