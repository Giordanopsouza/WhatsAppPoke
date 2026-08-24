# Tasks

File-based task tracker (`TRACKER_MODE: file`). **One markdown file per
atomic task**, committed to the repo. Root task files are the active plan.

## Folders

- `tasks/*.md` — current plan only (plus this README).
- `tasks/backlog/*.md` — valid work intentionally outside the current
  plan; move a file back to root when it is prioritized.
- `tasks/done/*.md` — completed historical work.

The frontmatter `status:` remains authoritative after a move.

## Format

`tasks/<NNN>-<slug>.md` (or the same filename under `backlog/` /
`done/`), where `NNN` is a zero-padded monotonic counter:

```
tasks/
├── 001-bootstrap-tui.md        # status: done
├── 002-agent-loop.md           # status: in-progress
└── 003-bash-tool.md            # status: pending
```

State lives in the `status:` frontmatter field — **not** in the filename
or folder. Folder placement communicates planning/archival intent.

## Task file shape

```markdown
---
id: 003-bash-tool
feature: tools          # the feature slug this task belongs to
status: pending         # pending | in-progress | done
---

# Bash tool

## Migration preflight

Before implementation, inspect the governing ADRs, this task, and its
directly dependent or consuming tasks.
Record the target end-state, temporary legacy bridges, forbidden legacy
dependencies in new code, the removal task for every bridge, and the
architecture test or CI check that enforces the boundary.

## Scope
One atomic, independently-shippable unit of work (1–2 sentences).

## Acceptance criteria
- [ ] ...

## Out of scope
- ...

## Log
### [PA] 2026-06-19 12:30 — Grooming
...
```

## Lifecycle

- **PA** grooming writes the file with `status: pending`.
- **SWE** starts it → `status: in-progress`.
- After the **Tester** PASSES and the task is committed → `status: done`.

Every agent **appends** (never rewrites) a timestamped entry to `## Log`: `### [ROLE] YYYY-MM-DD HH:MM — subject`. Roles: `PA`, `SWE`, `Tester`, `PR Reviewer`, `On-Call`.

Tasks are created and driven by the squid pipelines (`/plan`, `/implement-task`, `/implement-night`).
