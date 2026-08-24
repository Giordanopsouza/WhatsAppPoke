# One image, three start commands: `api` (uvicorn), `worker` (queue consumer),
# `analytics` (Streamlit). See railway.api.json / railway.worker.json /
# railway.analytics.json.

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# Dependencies first so app-code edits don't bust the layer.
# Plain COPY, no --mount: Railway's builder rejects bind mounts outright and
# requires cache mounts to carry an id of the form s/<service-id>-<path>, which
# a single Dockerfile shared by api, worker, and analytics cannot name for
# all three. Layer caching still applies; only the uv download cache is given up.
COPY pyproject.toml uv.lock README.md ./

RUN uv sync --locked --no-dev --no-install-project

COPY . /app

RUN uv sync --locked --no-dev


FROM python:3.12-slim-bookworm

WORKDIR /app

RUN useradd --create-home --uid 10001 app

COPY --from=builder --chown=app:app /app /app

# Run the venv's interpreter directly; no uv, no activation.
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER app

# Overridden by each service's startCommand.
CMD ["python", "-m", "app.worker"]
