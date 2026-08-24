"""Claim loop, process_job, run_worker."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import logfire
import sentry_sdk
from sqlalchemy import update

from app.database.models import Job, JobKind, JobStatus
from app.db import (
    claim_job,
    complete_job,
    fail_job,
    get_session,
    recover_stale_jobs,
)
from app.core.logutil import setup_logging
from app.core.observability import configure_observability
from app.worker._helpers import log, payload_str
from app.worker.handlers.automation_due import handle_automation_due
from app.worker.handlers.integration_notify import handle_integration_notify
from app.worker.handlers.reminder_due import handle_reminder_due

setup_logging()
configure_observability(service_name="wpp-agent-worker")

POLL_IDLE_SECONDS = 0.5

Handler = Callable[[Job], Awaitable[None]]

HANDLERS: dict[JobKind, Handler] = {
    JobKind.INTEGRATION_NOTIFY: handle_integration_notify,
    JobKind.REMINDER_DUE: handle_reminder_due,
    JobKind.AUTOMATION_DUE: handle_automation_due,
}


# Mark a job as permanently failed (no more retries).
async def _dead_letter(job: Job, *, reason: str) -> None:
    async with get_session() as session:
        await session.execute(
            update(Job)
            .where(Job.id == job.id, Job.status == JobStatus.RUNNING)
            .values(
                status=JobStatus.DEAD,
                attempts=job.attempts + 1,
                locked_at=None,
            )
        )
        await session.commit()
    log.error(
        "job_dead",
        extra={
            "event": "job_dead",
            "job_id": str(job.id),
            "contact_id": job.contact_id,
            "kind": job.kind,
            "reason": reason,
        },
    )


# Record a job failure and schedule a retry (or dead-letter if out of attempts).
async def _mark_failed(job: Job, exc: BaseException) -> None:
    async with get_session() as session:
        fresh = await session.get(Job, job.id)
        if fresh is None or fresh.status != JobStatus.RUNNING:
            await session.commit()
            return
        status = await fail_job(session, fresh)
        attempts = fresh.attempts + 1
        await session.commit()

    log.exception(
        "job_failed",
        extra={
            "event": "job_failed",
            "job_id": str(job.id),
            "contact_id": job.contact_id,
            "kind": job.kind,
            "status": status,
            "attempts": attempts,
        },
        exc_info=exc,
    )

    # DEAD-specific handling (e.g. fallback notification) has been retired.


# Run one claimed job through its handler, then mark it done or failed.
async def process_job(job: Job) -> None:
    handler = HANDLERS.get(job.kind)
    if handler is None:
        await _dead_letter(job, reason="unknown_kind")
        return

    try:
        await handler(job)
    except Exception as exc:
        sentry_sdk.capture_exception(exc)
        await _mark_failed(job, exc)
        return

    async with get_session() as session:
        await complete_job(session, job.id)
        await session.commit()


# Main worker loop: recover stale jobs, then poll and process due jobs forever.
async def run_worker() -> None:
    async with get_session() as session:
        recovered = await recover_stale_jobs(session)
        await session.commit()
    if recovered:
        log.info(
            "stale_jobs_recovered",
            extra={"event": "stale_jobs_recovered", "count": recovered},
        )

    log.info("worker_started", extra={"event": "worker_started"})

    while True:
        # Empty claims run twice a second. Without suppression, SQLAlchemy
        # emits a root ``connect`` + ``UPDATE`` span each time and buries
        # real job traces. Claim SQL is not useful once a job is in hand.
        with logfire.suppress_instrumentation():
            async with get_session() as session:
                job = await claim_job(session)
                await session.commit()

        if job is None:
            await asyncio.sleep(POLL_IDLE_SECONDS)
            continue

        provider_message_id = payload_str(job.payload or {}, "provider_message_id")
        log.info(
            "job_claimed",
            extra={
                "event": "job_claimed",
                "job_id": str(job.id),
                "contact_id": job.contact_id,
                "kind": job.kind,
                "attempts": job.attempts,
            },
        )

        with sentry_sdk.new_scope() as scope:
            scope.set_tag("contact_id", str(job.contact_id))
            if provider_message_id:
                scope.set_tag("provider_message_id", provider_message_id)

            with logfire.span(
                "job.execute",
                job_id=str(job.id),
                contact_id=job.contact_id,
                kind=str(job.kind),
                provider_message_id=provider_message_id,
            ):
                await process_job(job)


# Entry point when you run `python -m app.worker`.
def main() -> None:
    asyncio.run(run_worker())
