"""Durable Automation fire path: Execution then Interaction, never Twilio."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from app.agent.execution import LOCAL_TOOLKIT
from app.core.logutil import get_logger
from app.core.rrule import is_catch_up, next_occurrence_utc
from app.database.models import (
    Automation,
    AutomationLastRunStatus,
    AutomationStatus,
    Contact,
    ExecutionRun,
    ExecutionRunStatus,
    Job,
)
from app.db import (
    SessionLocal,
    complete_job,
    get_automation,
    get_session,
    record_automation_run,
    upsert_automation_due,
)
from app.services.execution import run_scheduled_execution


log = get_logger(__name__)

_LAST_RUN_FROM_EXECUTION = {
    ExecutionRunStatus.SUCCEEDED: AutomationLastRunStatus.SUCCEEDED,
    ExecutionRunStatus.FAILED: AutomationLastRunStatus.FAILED,
    ExecutionRunStatus.TIMED_OUT: AutomationLastRunStatus.TIMED_OUT,
    ExecutionRunStatus.CANCELLED: AutomationLastRunStatus.CANCELLED,
    ExecutionRunStatus.ABANDONED: AutomationLastRunStatus.FAILED,
}


# Unique key so the same automation occurrence never runs twice.
def automation_dedupe_key(automation_id: uuid.UUID, occurrence_at: datetime) -> str:
    stamp = occurrence_at.astimezone(timezone.utc).replace(microsecond=0).isoformat()
    return f"automation:{automation_id}:{stamp}"


# Parse a UUID from job payload JSON safely.
def _parse_uuid(raw: object) -> uuid.UUID | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return uuid.UUID(raw)
    except ValueError:
        return None


# Parse an ISO datetime from job payload JSON safely.
def _parse_occurrence(raw: object) -> datetime | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


# True when two datetimes are the same instant in UTC.
def _same_instant(left: datetime | None, right: datetime | None) -> bool:
    if left is None or right is None:
        return False
    return left.astimezone(timezone.utc) == right.astimezone(timezone.utc)


# Compute the next scheduled run time for an automation after now.
def _next_after_now(row: Automation, *, now: datetime) -> datetime | None:
    return next_occurrence_utc(
        rrule=row.rrule,
        timezone_name=row.timezone,
        after=now,
        dtstart=row.created_at,
    )


# Every automation job must belong to a contact.
def _require_contact_id(job: Job) -> int:
    if job.contact_id is None:
        raise RuntimeError(f"{job.kind} job {job.id} missing contact_id")
    return job.contact_id


# After a run finishes: update automation metadata, complete job, schedule next wake-up.
async def _close_wake_up(
    *,
    job: Job,
    row: Automation,
    occurrence_at: datetime,
    last_run_status: AutomationLastRunStatus,
    execution_run_id: uuid.UUID | None,
    was_catch_up: bool,
) -> None:
    now = datetime.now(timezone.utc)
    next_run_at = _next_after_now(row, now=now)
    async with get_session() as session:
        updated = await record_automation_run(
            session,
            automation_id=row.id,
            contact_id=row.contact_id,
            occurrence_at=occurrence_at,
            next_run_at=next_run_at,
            last_run_status=last_run_status,
            execution_run_id=execution_run_id,
            was_catch_up=was_catch_up,
        )
        await complete_job(session, job.id)
        if (
            updated is not None
            and updated.status == AutomationStatus.ACTIVE
            and next_run_at is not None
        ):
            await upsert_automation_due(
                session,
                contact_id=updated.contact_id,
                automation_id=updated.id,
                run_at=next_run_at,
            )
        await session.commit()


# Tell Interaction the automation was skipped (e.g. missing Gmail connection).
async def _reenter_skipped(
    *,
    contact_id: int,
    phone: str,
    automation: Automation,
    occurrence_at: datetime,
    detail: str,
) -> None:
    summary = json.dumps(
        {
            "automation_id": str(automation.id),
            "name": automation.name,
            "goal": automation.goal,
            "status": "skipped",
            "detail": detail,
            "occurrence_at": occurrence_at.isoformat(),
        },
        ensure_ascii=False,
    )
    from app.agent.interaction import run_interaction_event

    await run_interaction_event(
        contact_id=contact_id,
        phone=phone,
        provider_message_id=f"automation:{automation.id}:{occurrence_at.isoformat()}",
        internal_event_summary=summary,
        event_kind="internal_event",
    )


async def fire_due_automation(job: Job) -> None:
    """Claim one due automation, run Execution, re-enter Interaction, advance."""
    payload = job.payload or {}
    automation_id = _parse_uuid(payload.get("automation_id"))
    if automation_id is None:
        raise RuntimeError(f"automation_due job {job.id} missing automation_id")
    contact_id = _require_contact_id(job)

    async with get_session() as session:
        row = await get_automation(
            session, automation_id=automation_id, contact_id=contact_id
        )
        contact = await session.get(Contact, contact_id)
        await session.commit()

    if row is None or row.status != AutomationStatus.ACTIVE:
        log.info(
            "automation_due_noop",
            extra={
                "event": "automation_due_noop",
                "job_id": str(job.id),
                "contact_id": contact_id,
                "automation_id": str(automation_id),
                "status": None if row is None else str(row.status),
            },
        )
        return

    occurrence_at = _parse_occurrence(payload.get("occurrence_at")) or row.next_run_at
    if occurrence_at is None:
        raise RuntimeError(f"automation_due job {job.id} missing occurrence_at")

    if row.last_run_status is not None and _same_instant(
        row.last_occurrence_at, occurrence_at
    ):
        async with get_session() as session:
            await complete_job(session, job.id)
            if row.next_run_at is not None:
                await upsert_automation_due(
                    session,
                    contact_id=contact_id,
                    automation_id=row.id,
                    run_at=row.next_run_at,
                )
            await session.commit()
        return

    now = datetime.now(timezone.utc)
    was_catch_up = is_catch_up(scheduled_at=occurrence_at, now=now)
    required = [slug for slug in row.required_toolkits if slug]
    phone = contact.phone if contact is not None else ""
    tz = row.timezone or (contact.tz if contact is not None else "America/Sao_Paulo")

    outcome = await run_scheduled_execution(
        contact_id=contact_id,
        tz=tz,
        goal=row.goal,
        toolkit_scope=(LOCAL_TOOLKIT, *required),
        dedupe_key=automation_dedupe_key(row.id, occurrence_at),
        session_factory=SessionLocal,
    )
    if outcome.state == "unavailable":
        if phone:
            await _reenter_skipped(
                contact_id=contact_id,
                phone=phone,
                automation=row,
                occurrence_at=occurrence_at,
                detail=outcome.detail or "required toolkit is not connected",
            )
        await _close_wake_up(
            job=job,
            row=row,
            occurrence_at=occurrence_at,
            last_run_status=AutomationLastRunStatus.SKIPPED,
            execution_run_id=None,
            was_catch_up=was_catch_up,
        )
        return
    if outcome.state == "busy":
        raise RuntimeError("automation execution capacity full")

    last_status = AutomationLastRunStatus.SUCCEEDED
    if outcome.detail and outcome.detail in {s.value for s in ExecutionRunStatus}:
        mapped = _LAST_RUN_FROM_EXECUTION.get(ExecutionRunStatus(outcome.detail))
        if mapped is not None:
            last_status = mapped
    elif outcome.execution_run_id is not None:
        async with get_session() as session:
            run = await session.get(ExecutionRun, outcome.execution_run_id)
            await session.commit()
        if run is not None:
            last_status = _LAST_RUN_FROM_EXECUTION.get(
                run.status, AutomationLastRunStatus.SUCCEEDED
            )

    await _close_wake_up(
        job=job,
        row=row,
        occurrence_at=occurrence_at,
        last_run_status=last_status,
        execution_run_id=outcome.execution_run_id,
        was_catch_up=was_catch_up,
    )
    log.info(
        "automation_due_finished",
        extra={
            "event": "automation_due_finished",
            "job_id": str(job.id),
            "contact_id": contact_id,
            "automation_id": str(row.id),
            "catch_up": was_catch_up,
            "last_run_status": last_status.value,
        },
    )
