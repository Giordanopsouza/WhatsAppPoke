"""Logfire tracing + Sentry error reporting for api and worker processes.

Logfire gets agent/LLM/tool content so Runs and traces are inspectable.
Sentry still strips request bodies, cookies, and auth headers.
"""

from __future__ import annotations

import logging

import logfire
import sentry_sdk
from fastapi import FastAPI
from sentry_sdk.types import Event, Hint

from app.core.config import settings

_CONFIGURED = False


# Remove sensitive data from Sentry events before they leave the app.
def _sentry_before_send(event: Event, hint: Hint) -> Event | None:
    """Strip request payloads and auth material before events leave the app."""
    request = event.get("request")
    if isinstance(request, dict):
        request.pop("data", None)
        request.pop("cookies", None)
        headers = request.get("headers")
        if isinstance(headers, dict):
            for key in list(headers):
                if key.lower() in {
                    "authorization",
                    "cookie",
                    "x-api-key",
                    "x-client-token",
                }:
                    headers.pop(key, None)

    event.pop("user", None)
    return event


def configure_observability(*, service_name: str) -> None:
    """Boot Logfire + Sentry once per process. Safe to call from entrypoints."""
    global _CONFIGURED
    if _CONFIGURED:
        return

    logfire.configure(
        token=settings.logfire_token,
        service_name=service_name,
        # SQLAlchemy pool ``connect`` spans are debug; they drowned the live view.
        min_level="info",
    )

    logging.getLogger().addHandler(
        logfire.LogfireLoggingHandler(fallback=logging.NullHandler())
    )

    # Instrument libraries used by both processes. FastAPI is api-only.
    logfire.instrument_httpx(capture_all=True)
    logfire.instrument_requests()
    logfire.instrument_pydantic_ai(include_content=True)

    # Import after configure so the engine exists and spans attach cleanly.
    from app.db import engine

    logfire.instrument_sqlalchemy(engine=engine)

    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        send_default_pii=False,
        max_request_body_size="never",
        include_local_variables=False,
        traces_sample_rate=0.0,  # traces live in Logfire; Sentry is for errors
        before_send=_sentry_before_send,
    )

    _CONFIGURED = True


def instrument_fastapi_app(app: FastAPI) -> None:
    """Attach Logfire's FastAPI instrumentation (call after ``configure_observability``)."""
    logfire.instrument_fastapi(app, capture_headers=True)
