---
id: 027-github-ci-cd
feature: infra
status: pending
---

# GitHub CI/CD

## Migration preflight

Before implementation, inspect the relevant sections of `docs/plan.md`, the governing ADRs, this task, and its directly dependent or consuming tasks. Record:

- target end-state and contracts introduced here;
- legacy code allowed only as a temporary rollback bridge;
- legacy imports, data paths, and behaviors forbidden in new code;
- the task that removes each temporary bridge;
- an architecture test or CI check that enforces the boundary.

## Scope
Add a GitHub Actions workflow that gates PRs/pushes with the existing
test suite, and document how production CD works via Railway’s GitHub
integration (api + worker) so merges to `main` only ship when CI is
green.

## Acceptance criteria
- [ ] `.github/workflows/ci.yml`: on `pull_request` and `push` to `main`,
      checkout → install `uv` → `uv sync --frozen` (or project-equivalent)
      → `uv run pytest`. Fail the job on any test failure.
- [ ] Workflow uses a pinned Python version matching `pyproject.toml` /
      `.python-version` (whichever the repo already declares).
- [ ] No secrets required for the default CI job (unit tests must not
      need live `DATABASE_URL`, Twilio, Composio, or Gemini keys). If a
      test currently requires env, fix it to use fixtures/mocks or mark
      it so CI stays green without prod credentials.
- [ ] Branch protection on `main` (or equivalent repo setting): require
      the CI workflow to pass before merge. Document the one-time GitHub
      UI steps in `docs/deploy.md`.
- [ ] CD path documented in `docs/deploy.md`: Railway services already
      build from this GitHub repo / `main` (task 006); confirm both
      `api` and `worker` auto-deploy on merge, with api
      `preDeployCommand` still owning migrations. No second deploy
      pipeline in Actions unless Railway GitHub deploy is broken.
- [ ] Manual: open a throwaway PR that fails a trivial test → CI red;
      fix → CI green → merge → Railway deploys api + worker.

## Out of scope
- CI-gated evals / live LLM spend (015 + deferred in `docs/plan.md`).
- Staging environment or preview deploys per PR.
- Lint/format gate (ruff/mypy) — add only if already configured in-repo;
  do not introduce a new linter stack in this task.
- Deploy-from-Actions (`railway up` in GHA) while Railway’s native
  GitHub source deploy works.
- Autoscaling, multi-region, or release-train / canary.

## Depends on
- **006** (done): Docker + Railway api/worker already wired to the
  GitHub repo.

## Log
### [PA] 2026-08-12 11:19 — Grooming
Requested: create GitHub CI/CD. Scoped to Actions pytest gate + document
Railway auto-deploy from `main`; keep evals/staging out.
