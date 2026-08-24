"""Job queue: enqueue, claim, complete, fail, recover."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Job, JobKind, JobStatus
from app.db.session import STALE_LOCK_MINUTES, backoff_seconds


async def enqueue_integration_notify(
    session: AsyncSession,
    *,
    contact_id: int,
    payload: dict[str, Any] | None = None,
) -> None:
    """Enqueue a WhatsApp confirmation after a successful OAuth connect."""
    await session.execute(
        insert(Job).values(
            contact_id=contact_id,
            kind=JobKind.INTEGRATION_NOTIFY,
            payload=payload or {},
            run_at=func.now(),
            status=JobStatus.PENDING,
        )
    )


async def claim_job(session: AsyncSession) -> Job | None:
    """Claim the next due pending job with ``FOR UPDATE SKIP LOCKED``."""
    candidate = (
        select(Job.id)
        .where(
            Job.status == JobStatus.PENDING,
            Job.run_at <= func.now(),
        )
        .order_by(Job.run_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    stmt = (
        update(Job)
        .where(Job.id == candidate.scalar_subquery())
        .values(status=JobStatus.RUNNING, locked_at=func.now())
        .returning(Job)
    )
    return await session.scalar(stmt)


async def complete_job(session: AsyncSession, job_id: uuid.UUID) -> None:
    """Mark a claimed job as successfully done."""
    await session.execute(
        update(Job)
        .where(Job.id == job_id, Job.status == JobStatus.RUNNING)
        .values(status=JobStatus.DONE, locked_at=None)
    )


async def fail_job(
    session: AsyncSession,
    job: Job,
) -> JobStatus:
    """Record a failure: re-queue with backoff, or dead-letter at max attempts.

    Returns the status written (``pending`` or ``dead``).
    """
    attempts = job.attempts + 1
    if attempts >= job.max_attempts:
        await session.execute(
            update(Job)
            .where(Job.id == job.id, Job.status == JobStatus.RUNNING)
            .values(
                status=JobStatus.DEAD,
                attempts=attempts,
                locked_at=None,
            )
        )
        return JobStatus.DEAD

    await session.execute(
        update(Job)
        .where(Job.id == job.id, Job.status == JobStatus.RUNNING)
        .values(
            status=JobStatus.PENDING,
            attempts=attempts,
            locked_at=None,
            run_at=datetime.now(timezone.utc)
            + timedelta(seconds=backoff_seconds(attempts)),
        )
    )
    return JobStatus.PENDING


async def recover_stale_jobs(
    session: AsyncSession,
    *,
    older_than_minutes: int = STALE_LOCK_MINUTES,
) -> int:
    """Re-queue ``running`` jobs whose lock is older than the stale threshold.

    Counts the crashed run as an attempt: handlers rely on ``attempts > 0`` to
    know they may already have had a side effect (a reply that was sent before
    the crash), and a stale-recovered job has definitely run once.
    """
    stmt = (
        update(Job)
        .where(
            Job.status == JobStatus.RUNNING,
            Job.locked_at.is_not(None),
            Job.locked_at
            < func.now() - timedelta(minutes=older_than_minutes),
        )
        .values(
            status=JobStatus.PENDING,
            locked_at=None,
            attempts=Job.attempts + 1,
        )
    )
    result = await session.execute(stmt)
    return result.rowcount or 0
