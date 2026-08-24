"""User todo tasks."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import case, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Task, TaskStatus


async def create_task(
    session: AsyncSession,
    *,
    contact_id: int,
    title: str,
    due_at: datetime | None = None,
) -> Task:
    """Insert an open task for the contact."""
    row = Task(
        contact_id=contact_id,
        title=title,
        status=TaskStatus.OPEN,
        due_at=due_at,
    )
    session.add(row)
    await session.flush()
    return row


async def list_tasks_for_contact(
    session: AsyncSession,
    *,
    contact_id: int,
    include_done: bool = True,
) -> list[Task]:
    """Return tasks for listing: open first, then due_at, then created."""
    stmt = select(Task).where(Task.contact_id == contact_id)
    if not include_done:
        stmt = stmt.where(Task.status == TaskStatus.OPEN)
    # open before done; null due_at last within each status; oldest first.
    stmt = stmt.order_by(
        case((Task.status == TaskStatus.OPEN, 0), else_=1),
        Task.due_at.asc().nulls_last(),
        Task.created_at.asc(),
    )
    result = await session.scalars(stmt)
    return list(result.all())


async def complete_task_row(
    session: AsyncSession,
    *,
    task_id: uuid.UUID,
) -> Task | None:
    """Mark a task done. Returns the row, or None if missing / already done."""
    stmt = (
        update(Task)
        .where(Task.id == task_id, Task.status == TaskStatus.OPEN)
        .values(status=TaskStatus.DONE, updated_at=func.now())
        .returning(Task)
    )
    return await session.scalar(
        stmt, execution_options={"synchronize_session": False}
    )
