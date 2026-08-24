0004. Railway builder and runtime constraints shape the Dockerfile and start command
Status: Accepted
Date: 2026-08-07

Context
Task 006 ships one Docker image driven by two start commands (`api`,
`worker`). Three separate deploy failures on Railway traced back to
platform behaviour that differs from stock Docker/BuildKit and from
most PaaS conventions. Each fix looks redundant or unidiomatic in
isolation, so all three are recorded here to stop a future cleanup from
reintroducing the failure.

1. **Build mounts.** The dependency layer originally used
   `--mount=type=cache,target=/root/.cache/uv`. Railway's Metal builder
   rejected it: `missing an id argument`. Railway requires cache mounts
   to carry an id scoped to the service, which a single Dockerfile
   shared by two services cannot name for both. The follow-up fix
   swapped cache mounts for `--mount=type=bind`, which failed harder:
   `flag '--mount=type=bind' is missing a type=cache argument (other
   mount types are not supported)`. The builder supports neither form.

2. **Start command shell.** `railway.api.json` set
   `startCommand: "uvicorn app.main:app --host 0.0.0.0 --port $PORT"`.
   Railway execs the start command directly rather than through a
   shell, so uvicorn received the literal string `$PORT` and exited
   before binding: `Invalid value for '--port': '$PORT' is not a valid
   integer`. Every container died on start and the deployment failed at
   the healthcheck stage — which misattributes the fault to networking.

3. **`PORT` injection.** Railway is widely assumed to inject `PORT`.
   It did not here, before or after attaching a custom domain; the
   service variable list contains `RAILWAY_PUBLIC_DOMAIN` and
   `RAILWAY_SERVICE_API_URL` but no `PORT`.

Decision
Accept the platform constraints rather than work around them.

1. Install dependencies with a plain `COPY pyproject.toml uv.lock
   README.md ./` followed by `uv sync`. **No `--mount` of any kind in
   the Dockerfile.** Docker layer caching still applies — only uv's
   download cache is given up, which costs seconds on a cold build.
2. Wrap any start command that references an environment variable in
   `sh -c '...'` so expansion happens.
3. Set `PORT=8000` explicitly as a service variable and pin the custom
   domain's target port to `8000`. The start command keeps a
   `${PORT:-8000}` fallback so the two cannot disagree even if Railway
   begins injecting `PORT` later.

Consequences
Positive:
- The build is portable: nothing depends on BuildKit mount semantics,
  so the same Dockerfile builds locally and on Railway.
- One Dockerfile keeps serving both services; no per-service cache id
  means no per-service Dockerfile.
- Port binding is deterministic and independent of Railway's
  auto-detection.

Negative / tradeoffs:
- Cold builds re-download the dependency set (no uv cache). Warm layer
  caching hides this whenever `pyproject.toml`/`uv.lock` are unchanged.
- `COPY` of the lock files is a real layer, so any touch of those files
  busts dependency install — same as the mount version, but now also on
  metadata-only changes.
- `sh -c` adds a shell process between the container and uvicorn.
  Signals still reach uvicorn because `sh -c` with a single command
  execs it, but a multi-command start string would break that.
- Pinning `PORT` diverges from the PaaS convention of reading an
  injected port. If Railway later injects a different `PORT`, the
  explicit variable wins — intended, but worth knowing.

Rejected alternatives:
- Per-service Dockerfiles so each could name its own cache id — two
  files to keep in sync for a seconds-scale cache win.
- Moving the start command into the Dockerfile `CMD` to dodge the shell
  issue — the image is deliberately shared, and `CMD` cannot differ per
  service.
- Relying on Railway's port auto-detection — works, but leaves the
  domain's target port and the bound port coupled implicitly.

See also: `docs/deploy.md` (runbook), `tasks/006-deploy-two-services.md`.
