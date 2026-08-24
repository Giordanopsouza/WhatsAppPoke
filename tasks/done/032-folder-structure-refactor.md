---
id: 032-folder-structure-refactor
feature: infra
status: done
---

# Folder structure refactor

## Migration preflight

Before implementation, inspect the relevant sections of `docs/plan.md`, the governing ADRs, this task, and its directly dependent or consuming tasks. Record:

- target end-state and contracts introduced here;
- legacy code allowed only as a temporary rollback bridge;
- legacy imports, data paths, and behaviors forbidden in new code;
- the task that removes each temporary bridge;
- an architecture test or CI check that enforces the boundary.

## Scope
Reorganize the growing flat `app/` package into layered subpackages
(`core/`, `db/`, `transport/`, `connect/`, `api/`, `worker/`) without
breaking runtime entrypoints, imports, tests, Alembic, or Railway deploy
configs. Ship as **six small PRs** (one per phase below), each with
`uv run pytest` green before merge.

## Why now
- `app/db.py` (~1060 lines) is a god module — all SQL lives in one file.
- `app/worker.py` (~780 lines) mixes claim loop + five job handlers.
- Ten+ modules sit flat at `app/` root with no semantic grouping.
- Docs clutter: `docs/poke_*.md`, duplicate ADR, missing `docs/plan.md`
  reference, orphan `README 2.md`, untracked `.deepsec/` noise.

## Invariants (must NOT break)

| Entrypoint / import | Used by |
|---|---|
| `uvicorn app.main:app` | Railway api, `AGENTS.md`, dev |
| `python -m app.worker` | Railway worker, Dockerfile CMD |
| `from app.db import …` | main, worker, agent, tests |
| `from app.config import settings` | api, worker, alembic, agent |
| `streamlit run analytics/dashboard.py` | Railway analytics |
| `alembic upgrade head` | api preDeployCommand |

**Python rule:** `foo.py` and `foo/` **cannot coexist**. When creating
a package, delete the `.py` file in the **same commit** and re-export
public symbols from `foo/__init__.py`.

## Target structure

```text
app/
├── core/                    # shared infra (moved from app root)
│   ├── config.py
│   ├── logutil.py
│   ├── observability.py
│   └── timeutil.py
├── transport/               # wire format only (no DB, no agent)
│   └── twilio_wa.py
├── connect/                 # OAuth via WhatsApp links
│   ├── pages.py             # ex connect_pages.py
│   └── token.py             # ex connect_token.py
├── db/                      # split from monolithic db.py
│   ├── __init__.py          # re-exports entire public API
│   ├── session.py           # engine, SessionLocal, get_session
│   ├── contacts.py
│   ├── messages.py
│   ├── jobs.py
│   ├── integrations.py
│   ├── reminders.py
│   ├── tasks.py
│   └── briefing.py
├── services/                # pure domain logic (no SQL, no HTTP)
│   └── briefing.py          # ex app/briefing.py
├── api/
│   └── main.py              # ex app/main.py
├── worker/
│   ├── __init__.py          # re-exports symbols tests patch
│   ├── __main__.py          # python -m app.worker
│   ├── loop.py              # claim loop, process_job, run_worker
│   └── handlers/
│       ├── agent_turn.py
│       ├── integration_notify.py
│       ├── reminder_due.py
│       ├── outbound_sweep.py
│       └── outbound_due.py
├── agent/                   # unchanged
├── integrations/            # unchanged
└── database/models/         # unchanged

# Shims at old paths (thin re-exports — keep until cleanup PR):
app/main.py          → from app.api.main import app
app/config.py        → from app.core.config import *
app/twilio_wa.py     → from app.transport.twilio_wa import *
app/connect_pages.py → from app.connect.pages import *
app/connect_token.py → from app.connect.token import *
```

Dependency flow (respect `AGENTS.md` module boundaries):

```text
api/     → db/, transport/, connect/, integrations/  (never agent LLM)
worker/  → db/, agent/, services/, transport/
agent/   → integrations/, services/, db/ (read paths only)
analytics/ stays at repo root — never imports app.config
```

## Execution plan — six PRs

Branch naming: `032-folder-structure-prN` (or one branch `032-folder-structure`
if the implementer prefers stacking — **prefer separate PRs** per phase).

### PR 0 — Housekeeping (no logic changes)

- [ ] Move `docs/poke_*.md` → `docs/reference/prompts/`.
- [ ] Delete duplicate `docs/adr/0006-google-oauth-via-whatsapp-connect-links 2.md`.
- [ ] Delete or merge orphan `README 2.md` at repo root.
- [ ] Fix `AGENTS.md`: either restore a minimal `docs/plan.md` (pointer to
      `docs/glossary.md` + ADRs) or remove the broken `docs/plan.md` row.
- [ ] Extend `.gitignore`:

  ```gitignore
  .deepsec/
  .pytest_cache/
  .understand-anything/
  ```

- [ ] `uv run pytest` green.

### PR 1 — `app/core/`

```bash
mkdir -p app/core
git mv app/config.py app/core/config.py
git mv app/logutil.py app/core/logutil.py
git mv app/observability.py app/core/observability.py
git mv app/timeutil.py app/core/timeutil.py
```

- [ ] Fix **internal** imports inside moved files (e.g. observability imports
      config from `app.core.config`).
- [ ] Create shim modules at old paths:

  ```python
  # app/config.py
  from app.core.config import *  # noqa: F403
  from app.core.config import settings
  ```

  Same pattern for `logutil`, `observability`, `timeutil` if anything
  imports them as `app.logutil` etc.

- [ ] Smoke:

  ```bash
  uv run python -c "from app.config import settings"
  uv run python -c "from app.core.config import settings"
  uv run pytest
  ```

### PR 2 — `app/db/` split (highest ROI)

- [ ] Create `app/db/` package; move functions from `app/db.py` by domain:
      `session`, `contacts`, `messages`, `jobs`, `integrations`, `reminders`,
      `tasks`, `briefing`.
- [ ] `app/db/__init__.py` re-exports **every** symbol currently imported
      elsewhere. Discover the list with:

  ```bash
  rg "from app\.db import" --type py
  rg "import app\.db" --type py
  ```

- [ ] Delete `app/db.py` in the same commit.
- [ ] Avoid circular imports: `session.py` must not import handlers;
      domain modules import from `session.py`.
- [ ] **Do not** change consumer imports in this PR — shims via `__init__.py`
      only.
- [ ] Smoke:

  ```bash
  uv run python -c "from app.db import get_session, claim_job, enqueue_agent_turn"
  uv run pytest
  ```

### PR 3 — `app/transport/` + `app/connect/`

```bash
mkdir -p app/transport app/connect
git mv app/twilio_wa.py app/transport/twilio_wa.py
git mv app/connect_pages.py app/connect/pages.py
git mv app/connect_token.py app/connect/token.py
```

- [ ] Create shims: `app/twilio_wa.py`, `app/connect_pages.py`,
      `app/connect_token.py`.
- [ ] Update imports in `main`, `worker`, `agent/tools.py` **or** rely on shims
      (shims are enough for green tests; updating consumers is optional here).
- [ ] `uv run pytest` green.

### PR 4 — `app/worker/` package

**Most delicate PR** — tests use string patches like `patch("app.worker.send_text")`.

- [ ] Create `app/worker/` package; **delete** `app/worker.py` same commit.
- [ ] `app/worker/__main__.py`:

  ```python
  from app.worker.loop import main
  main()
  ```

- [ ] `app/worker/loop.py`: claim loop, `process_job`, `run_worker`, `main`.
- [ ] `app/worker/handlers/*.py`: one file per `JobKind` handler.
- [ ] `app/worker/__init__.py` re-exports every name tests patch:

  ```bash
  rg 'patch\("app\.worker\.' tests/ -o | sort -u
  ```

  Typical list: `get_session`, `load_recent_messages`, `get_briefing_state`,
  `run_turn`, `send_text`, `insert_outbound_message`, `outbound_exists_since`,
  `get_reminder`, `claim_reminder_for_send`, `send_content_template`,
  `compose_reminder_reply`, etc.

  Re-export from the handler module or from the original source module so
  `patch("app.worker.X")` still resolves.

- [ ] Alternative (if re-exports get messy): update test patches to target
      the handler module where the name is used — do this in the **same PR**.
- [ ] Smoke:

  ```bash
  uv run python -c "import app.worker"
  uv run python -m app.worker &  sleep 1; kill $!   # imports, then exit
  uv run pytest
  ```

### PR 5 — `app/api/` + `app/services/`

```bash
mkdir -p app/api app/services
git mv app/main.py app/api/main.py
git mv app/briefing.py app/services/briefing.py
```

- [ ] Shim `app/main.py`:

  ```python
  from app.api.main import app  # noqa: F401
  ```

- [ ] Update `app/services/briefing.py` imports (`app.timeutil` → shim or
      `app.core.timeutil`).
- [ ] Update `app/db/briefing.py` (if split) to import from
      `app.services.briefing` for pure logic constants/helpers.
- [ ] Railway / docs **unchanged** (`app.main:app`).
- [ ] `uv run pytest` green.

### PR 6 — Cleanup (optional, can defer)

- [ ] Migrate internal imports from shims → canonical paths
      (`app.core.config` instead of `app.config`).
- [ ] Remove shims when `rg "from app\.config import"` (etc.) returns zero
      outside the shim files themselves.
- [ ] Mirror `tests/` structure under subfolders (`tests/worker/`, `tests/api/`,
      `tests/db/`) — move files, fix imports, keep pytest discovery working.
- [ ] Update `AGENTS.md` module boundary table with new paths.
- [ ] `uv run pytest` green.

## Acceptance criteria (overall)

- [ ] All six PRs merged (PR 6 optional but documented if skipped).
- [ ] `uv run pytest` passes on `main` after each merge.
- [ ] Runtime smoke on `main`:
      - `uv run python -c "import app.main; import app.worker; import app.db"`
      - `uv run uvicorn app.main:app --host 127.0.0.1 --port 9999` starts (curl
        `/health` if available, then stop).
- [ ] Railway configs **unchanged** unless shims removed intentionally:
      `railway.api.json` still `uvicorn app.main:app …`;
      `railway.worker.json` still `python -m app.worker`.
- [ ] Alembic: `uv run alembic check` or `alembic upgrade head` against dev DB
      still works (`alembic/env.py` imports `app.config` + models).
- [ ] No `foo.py` + `foo/` directory pairs left in `app/`.
- [ ] `app/db/` has no single file > ~400 lines (split further if needed).
- [ ] `AGENTS.md` module table reflects new layout (PR 5 or 6).

## Verification commands (run every PR)

```bash
# Import smoke
uv run python -c "
import importlib
for m in ['app.main', 'app.worker', 'app.db', 'app.agent.loop', 'app.agent.tools']:
    importlib.import_module(m)
    print('OK', m)
"

# Tests
uv run pytest

# Find remaining old-path imports (during cleanup)
rg 'from app\.(config|db|twilio_wa|connect_pages|connect_token|briefing) import' --type py
rg 'patch\(\"app\.(main|worker)\.' tests/
```

## Out of scope

- Splitting `app/agent/tools.py` into `app/agent/tools/` (do when > ~500 lines
  in a future task).
- Moving `analytics/` inside `app/`.
- Changing Railway service count or start commands (unless shims removed).
- New abstractions (`repositories/`, `use_cases/`, Redis, Temporal).
- Renaming `app/database/models/` → `app/models/` (ORM path is fine).
- `deploy/` folder for Dockerfile + railway json (optional future tidy).

## Depends on

- **027** (pending): CI workflow makes refactor safer — prefer merging 027
  first, but not a hard blocker if implementer runs pytest locally each PR.

## Does not block

- **031** (in progress): briefing feature can land before or during refactor;
  if 031 merges first, rebase each PR and ensure `app/db/briefing.py` +
  `app/worker/handlers/outbound_*.py` include 031's code.

## Git workflow

Per `AGENTS.md`: one task → branches → PRs. Either:

- **Preferred:** six sequential PRs from fresh `main` (`032-folder-structure-pr1` …
  `pr6`), each merged before the next starts; or
- One branch `032-folder-structure` with six commits if the user wants a single
  review — still run pytest between commits.

Do not stack unrelated tasks on the refactor branch.

## Log
### [PA] 2026-08-13 16:20 — Grooming
Architecture pass: flat `app/` root + god `db.py` + monolithic `worker.py`
are the main clarity bottlenecks. Planned six incremental PRs using package
re-exports and shims so `app.main:app`, `python -m app.worker`, and
`from app.db import …` stay stable. Included housekeeping (docs, gitignore)
and explicit test-patch strategy for worker split.
