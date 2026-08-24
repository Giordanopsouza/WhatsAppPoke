"""Scheduled WhatsApp reminders."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import case, func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Job, JobKind, JobStatus, Reminder, ReminderStatus


async def create_reminder(
    session: AsyncSession,
    *,
    contact_id: int,
    body: str,
    due_at: datetime,
) -> Reminder:
    """Insert an active reminder (caller enqueues ``reminder_due`` in same txn)."""
    row = Reminder(
        contact_id=contact_id,
        body=body,
        due_at=due_at,
        status=ReminderStatus.ACTIVE,
    )
    session.add(row)
    await session.flush()
    return row


async def enqueue_reminder_due(
    session: AsyncSession,
    *,
    contact_id: int,
    reminder_id: uuid.UUID,
    run_at: datetime,
) -> None:
    """Wake-up job for a reminder. ``run_at`` is the reminder's ``due_at``."""
    await session.execute(
        insert(Job).values(
            contact_id=contact_id,
            kind=JobKind.REMINDER_DUE,
            payload={"reminder_id": str(reminder_id)},
            run_at=run_at,
            status=JobStatus.PENDING,
        )
    )


async def list_reminders_for_contact(
    session: AsyncSession,
    *,
    contact_id: int,
    include_inactive: bool = True,
) -> list[Reminder]:
    """Active first, then soonest ``due_at``."""
    stmt = select(Reminder).where(Reminder.contact_id == contact_id)
    if not include_inactive:
        stmt = stmt.where(Reminder.status == ReminderStatus.ACTIVE)
    stmt = stmt.order_by(
        case((Reminder.status == ReminderStatus.ACTIVE, 0), else_=1),
        Reminder.due_at.asc(),
        Reminder.created_at.asc(),
    )
    result = await session.scalars(stmt)
    return list(result.all())


async def cancel_reminder_row(
    session: AsyncSession,
    *,
    reminder_id: uuid.UUID,
) -> Reminder | None:
    """Mark active → cancelled. Job wake-up is left alone (check-at-fire)."""
    stmt = (
        update(Reminder)
        .where(
            Reminder.id == reminder_id,
            Reminder.status == ReminderStatus.ACTIVE,
        )
        .values(status=ReminderStatus.CANCELLED, updated_at=func.now())
        .returning(Reminder)
    )
    return await session.scalar(
        stmt, execution_options={"synchronize_session": False}
    )


# Look up one reminder by id.
async def get_reminder(
    session: AsyncSession,
    *,
    reminder_id: uuid.UUID,
) -> Reminder | None:
    return await session.get(Reminder, reminder_id)


async def claim_reminder_for_send(
    session: AsyncSession,
    *,
    reminder_id: uuid.UUID,
) -> Reminder | None:
    """Atomically active → sent so a retry cannot double-ping."""
    stmt = (
        update(Reminder)
        .where(
            Reminder.id == reminder_id,
            Reminder.status == ReminderStatus.ACTIVE,
        )
        .values(
            status=ReminderStatus.SENT,
            sent_at=func.now(),
            updated_at=func.now(),
        )
        .returning(Reminder)
    )
    return await session.scalar(
        stmt, execution_options={"synchronize_session": False}
    )


async def release_reminder_claim(
    session: AsyncSession,
    *,
    reminder_id: uuid.UUID,
) -> None:
    """Undo a send claim when Twilio/compose failed before outbound persist."""
    await session.execute(
        update(Reminder)
        .where(
            Reminder.id == reminder_id,
            Reminder.status == ReminderStatus.SENT,
        )
        .values(
            status=ReminderStatus.ACTIVE,
            sent_at=None,
            updated_at=func.now(),
        )
        .execution_options(synchronize_session=False)
    )
