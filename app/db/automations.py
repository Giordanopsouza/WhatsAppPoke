"""Contact-scoped RRULE automations and their durable wake-up jobs."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import case, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import (
    Automation,
    AutomationLastRunStatus,
    AutomationStatus,
    Job,
    JobKind,
    JobStatus,
)


# Build the JSON payload stored on an automation_due job.
def _occurrence_payload(automation_id: uuid.UUID, run_at: datetime) -> dict[str, str]:
    occurrence = run_at.astimezone(timezone.utc).isoformat()
    return {
        "automation_id": str(automation_id),
        "occurrence_at": occurrence,
    }


# Save a new recurring automation row for one contact.
async def create_automation_row(
    session: AsyncSession,
    *,
    contact_id: int,
    name: str,
    goal: str,
    rrule: str,
    timezone_name: str,
    required_toolkits: list[str],
    next_run_at: datetime | None,
) -> Automation:
    row = Automation(
        contact_id=contact_id,
        name=name,
        goal=goal,
        rrule=rrule,
        timezone=timezone_name,
        required_toolkits=required_toolkits,
        status=AutomationStatus.ACTIVE,
        next_run_at=next_run_at,
    )
    session.add(row)
    await session.flush()
    return row


# Load one automation by id (optionally scoped to a contact).
async def get_automation(
    session: AsyncSession,
    *,
    automation_id: uuid.UUID,
    contact_id: int | None = None,
) -> Automation | None:
    stmt = select(Automation).where(Automation.id == automation_id)
    if contact_id is not None:
        stmt = stmt.where(Automation.contact_id == contact_id)
    return await session.scalar(stmt)


# List all automations for a contact (active first, then by next run time).
async def list_automations_for_contact(
    session: AsyncSession,
    *,
    contact_id: int,
    include_cancelled: bool = True,
) -> list[Automation]:
    stmt = select(Automation).where(Automation.contact_id == contact_id)
    if not include_cancelled:
        stmt = stmt.where(Automation.status != AutomationStatus.CANCELLED)
    stmt = stmt.order_by(
        case(
            (Automation.status == AutomationStatus.ACTIVE, 0),
            (Automation.status == AutomationStatus.PAUSED, 1),
            else_=2,
        ),
        Automation.next_run_at.asc().nulls_last(),
        Automation.created_at.asc(),
    )
    result = await session.scalars(stmt)
    return list(result.all())


# Find the pending worker job that will fire this automation.
async def get_pending_automation_due(
    session: AsyncSession,
    *,
    automation_id: uuid.UUID,
) -> Job | None:
    stmt = (
        select(Job)
        .where(
            Job.kind == JobKind.AUTOMATION_DUE,
            Job.status == JobStatus.PENDING,
            Job.payload["automation_id"].astext == str(automation_id),
        )
        .limit(1)
    )
    return await session.scalar(stmt)


async def upsert_automation_due(
    session: AsyncSession,
    *,
    contact_id: int,
    automation_id: uuid.UUID,
    run_at: datetime,
) -> bool:
    """Ensure one pending wake-up for this automation. Returns True if inserted."""
    payload = _occurrence_payload(automation_id, run_at)
    existing = await get_pending_automation_due(session, automation_id=automation_id)
    if existing is not None:
        await session.execute(
            update(Job)
            .where(Job.id == existing.id, Job.status == JobStatus.PENDING)
            .values(run_at=run_at, payload=payload)
        )
        return False

    stmt = insert(Job).values(
        contact_id=contact_id,
        kind=JobKind.AUTOMATION_DUE,
        payload=payload,
        run_at=run_at,
        status=JobStatus.PENDING,
    )
    for _ in range(2):
        try:
            async with session.begin_nested():
                await session.execute(stmt)
            return True
        except IntegrityError:
            existing = await get_pending_automation_due(
                session, automation_id=automation_id
            )
            if existing is not None:
                await session.execute(
                    update(Job)
                    .where(Job.id == existing.id, Job.status == JobStatus.PENDING)
                    .values(run_at=run_at, payload=payload)
                )
                return False
    return False


# Change automation status (pause, resume, cancel) with valid transitions only.
async def set_automation_status(
    session: AsyncSession,
    *,
    automation_id: uuid.UUID,
    contact_id: int,
    status: AutomationStatus,
    next_run_at: datetime | None = None,
) -> Automation | None:
    values: dict[str, object] = {
        "status": status,
        "updated_at": func.now(),
    }
    if next_run_at is not None:
        values["next_run_at"] = next_run_at
    if status == AutomationStatus.CANCELLED:
        values["next_run_at"] = None
    if status == AutomationStatus.PAUSED:
        allowed = (AutomationStatus.ACTIVE,)
    elif status == AutomationStatus.ACTIVE:
        allowed = (AutomationStatus.PAUSED,)
    else:
        allowed = (AutomationStatus.ACTIVE, AutomationStatus.PAUSED)
    stmt = (
        update(Automation)
        .where(
            Automation.id == automation_id,
            Automation.contact_id == contact_id,
            Automation.status.in_(allowed),
        )
        .values(**values)
        .returning(Automation)
    )
    return await session.scalar(
        stmt, execution_options={"synchronize_session": False}
    )


async def record_automation_run(
    session: AsyncSession,
    *,
    automation_id: uuid.UUID,
    contact_id: int,
    occurrence_at: datetime,
    next_run_at: datetime | None,
    last_run_status: AutomationLastRunStatus,
    execution_run_id: uuid.UUID | None,
    was_catch_up: bool,
) -> Automation | None:
    """Stamp last-run/catch-up metadata and the next UTC occurrence."""
    stmt = (
        update(Automation)
        .where(
            Automation.id == automation_id,
            Automation.contact_id == contact_id,
            Automation.status == AutomationStatus.ACTIVE,
        )
        .values(
            last_run_at=func.now(),
            last_run_status=last_run_status,
            last_occurrence_at=occurrence_at,
            last_execution_run_id=execution_run_id,
            last_run_was_catch_up=was_catch_up,
            next_run_at=next_run_at,
            updated_at=func.now(),
        )
        .returning(Automation)
    )
    return await session.scalar(
        stmt, execution_options={"synchronize_session": False}
    )
