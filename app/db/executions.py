"""Persistence operations for detached Execution runs and Interaction outbounds."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import (
    Contact,
    ExecutionEvent,
    ExecutionRun,
    ExecutionRunStatus,
    Message,
    MessageDeliveryState,
    MessageDirection,
)


ACTIVE_EXECUTION_STATUSES = (
    ExecutionRunStatus.PENDING,
    ExecutionRunStatus.RUNNING,
    ExecutionRunStatus.CANCEL_REQUESTED,
)
TERMINAL_EXECUTION_STATUSES = (
    ExecutionRunStatus.SUCCEEDED,
    ExecutionRunStatus.FAILED,
    ExecutionRunStatus.TIMED_OUT,
    ExecutionRunStatus.CANCELLED,
    ExecutionRunStatus.ABANDONED,
)


async def create_or_get_execution_run(
    session: AsyncSession,
    *,
    contact_id: int,
    goal: str,
    toolkit_scope: Sequence[str],
    dedupe_key: str,
    max_active_runs: int = 2,
) -> tuple[ExecutionRun | None, bool]:
    """Atomically return a deduped run or create one when capacity permits.

    The contact row lock serializes create/count/dedupe decisions for one
    tenant. Callers commit this session after any corresponding task is
    registered; no execution work begins inside this helper.
    """
    await session.scalar(
        select(Contact.id).where(Contact.id == contact_id).with_for_update()
    )
    existing_stmt = (
        select(ExecutionRun)
        .where(
            ExecutionRun.contact_id == contact_id,
            ExecutionRun.dedupe_key == dedupe_key,
            ExecutionRun.status.in_(ACTIVE_EXECUTION_STATUSES),
        )
        .order_by(ExecutionRun.created_at.desc())
        .limit(1)
    )
    existing = await session.scalar(existing_stmt)
    if existing is not None:
        return existing, False

    if await count_active_execution_runs(session, contact_id=contact_id) >= max_active_runs:
        return None, False

    normalized_scope = sorted(set(toolkit_scope))
    stmt = (
        insert(ExecutionRun)
        .values(
            contact_id=contact_id,
            goal=goal,
            toolkit_scope=normalized_scope,
            dedupe_key=dedupe_key,
            status=ExecutionRunStatus.PENDING,
        )
        .returning(ExecutionRun)
    )
    return await session.scalar(stmt), True


async def get_execution_run_by_dedupe(
    session: AsyncSession,
    *,
    contact_id: int,
    dedupe_key: str,
) -> ExecutionRun | None:
    """Latest run for one contact+dedupe key, including terminal rows."""
    stmt = (
        select(ExecutionRun)
        .where(
            ExecutionRun.contact_id == contact_id,
            ExecutionRun.dedupe_key == dedupe_key,
        )
        .order_by(ExecutionRun.created_at.desc())
        .limit(1)
    )
    return await session.scalar(stmt)


async def reclaim_execution_run_for_retry(
    session: AsyncSession,
    *,
    execution_run_id: uuid.UUID,
    contact_id: int,
) -> ExecutionRun | None:
    """Return a stuck running row to pending so the same run can be retried.

    Used when a durable ``automation_due`` job is retried after a worker crash.
    The run id stays the same so this is not a duplicate Execution.
    """
    stmt = (
        update(ExecutionRun)
        .where(
            ExecutionRun.id == execution_run_id,
            ExecutionRun.contact_id == contact_id,
            ExecutionRun.status.in_(
                (ExecutionRunStatus.RUNNING, ExecutionRunStatus.CANCEL_REQUESTED)
            ),
        )
        .values(
            status=ExecutionRunStatus.PENDING,
            started_at=None,
            cancel_requested=False,
            updated_at=func.now(),
        )
        .returning(ExecutionRun)
    )
    return await session.scalar(stmt, execution_options={"synchronize_session": False})


async def count_active_execution_runs(
    session: AsyncSession, *, contact_id: int
) -> int:
    """Count pending/running/cancellation-in-progress runs for one contact."""
    stmt = select(func.count()).select_from(ExecutionRun).where(
        ExecutionRun.contact_id == contact_id,
        ExecutionRun.status.in_(ACTIVE_EXECUTION_STATUSES),
    )
    return int(await session.scalar(stmt) or 0)


async def claim_execution_run(
    session: AsyncSession, *, execution_run_id: uuid.UUID, contact_id: int
) -> ExecutionRun | None:
    """Atomically transition one pending run to running."""
    stmt = (
        update(ExecutionRun)
        .where(
            ExecutionRun.id == execution_run_id,
            ExecutionRun.contact_id == contact_id,
            ExecutionRun.status == ExecutionRunStatus.PENDING,
            ExecutionRun.cancel_requested.is_(False),
        )
        .values(
            status=ExecutionRunStatus.RUNNING,
            started_at=func.now(),
            updated_at=func.now(),
        )
        .returning(ExecutionRun)
    )
    return await session.scalar(stmt, execution_options={"synchronize_session": False})


async def finish_execution_run(
    session: AsyncSession,
    *,
    execution_run_id: uuid.UUID,
    contact_id: int,
    status: ExecutionRunStatus,
    result: dict[str, Any] | None = None,
    error: str | None = None,
) -> ExecutionRun | None:
    """Finish one active run exactly once, retaining a compact outcome."""
    if status not in TERMINAL_EXECUTION_STATUSES:
        raise ValueError(f"Execution terminal status required, got {status!r}")
    stmt = (
        update(ExecutionRun)
        .where(
            ExecutionRun.id == execution_run_id,
            ExecutionRun.contact_id == contact_id,
            ExecutionRun.status.in_(ACTIVE_EXECUTION_STATUSES),
        )
        .values(
            status=status,
            result=result,
            error=error,
            finished_at=func.now(),
            updated_at=func.now(),
        )
        .returning(ExecutionRun)
    )
    return await session.scalar(stmt, execution_options={"synchronize_session": False})


async def append_execution_event(
    session: AsyncSession,
    *,
    contact_id: int,
    execution_run_id: uuid.UUID,
    kind: str,
    payload: dict[str, Any] | None = None,
) -> ExecutionEvent:
    """Append an internal event. It deliberately does not create a message."""
    stmt = (
        insert(ExecutionEvent)
        .values(
            contact_id=contact_id,
            execution_run_id=execution_run_id,
            kind=kind,
            payload=payload or {},
        )
        .returning(ExecutionEvent)
    )
    return (await session.scalars(stmt)).one()


async def mark_execution_event_processed(
    session: AsyncSession, *, event_id: uuid.UUID, contact_id: int
) -> ExecutionEvent | None:
    """Mark one contact-scoped event processed; another contact cannot claim it."""
    stmt = (
        update(ExecutionEvent)
        .where(
            ExecutionEvent.id == event_id,
            ExecutionEvent.contact_id == contact_id,
            ExecutionEvent.processed_at.is_(None),
        )
        .values(processed_at=func.now())
        .returning(ExecutionEvent)
    )
    return await session.scalar(stmt, execution_options={"synchronize_session": False})


async def request_execution_cancellation(
    session: AsyncSession, *, execution_run_id: uuid.UUID, contact_id: int
) -> ExecutionRun | None:
    """Record a cooperative cancellation request without deleting audit state."""
    stmt = (
        update(ExecutionRun)
        .where(
            ExecutionRun.id == execution_run_id,
            ExecutionRun.contact_id == contact_id,
            ExecutionRun.status.in_(
                (ExecutionRunStatus.PENDING, ExecutionRunStatus.RUNNING)
            ),
        )
        .values(
            cancel_requested=True,
            status=ExecutionRunStatus.CANCEL_REQUESTED,
            updated_at=func.now(),
        )
        .returning(ExecutionRun)
    )
    return await session.scalar(stmt, execution_options={"synchronize_session": False})


async def abandon_stale_execution_runs(
    session: AsyncSession, *, older_than: timedelta
) -> int:
    """Mark orphaned started runs abandoned after an API restart/fuse expiry."""
    stmt = (
        update(ExecutionRun)
        .where(
            ExecutionRun.status.in_(
                (ExecutionRunStatus.RUNNING, ExecutionRunStatus.CANCEL_REQUESTED)
            ),
            ExecutionRun.started_at.is_not(None),
            ExecutionRun.started_at < func.now() - older_than,
        )
        .values(
            status=ExecutionRunStatus.ABANDONED,
            finished_at=func.now(),
            updated_at=func.now(),
        )
    )
    result = await session.execute(stmt)
    return result.rowcount or 0


async def reserve_interaction_outbound(
    session: AsyncSession,
    *,
    contact_id: int,
    interaction_run_id: uuid.UUID,
    sequence: int,
    body: str,
) -> tuple[Message, bool]:
    """Reserve a visible outbound once, before Twilio is called."""
    stmt = (
        insert(Message)
        .values(
            contact_id=contact_id,
            direction=MessageDirection.OUT,
            body=body,
            interaction_run_id=interaction_run_id,
            outbound_sequence=sequence,
            delivery_state=MessageDeliveryState.RESERVED,
        )
        .on_conflict_do_nothing(
            index_elements=[
                Message.contact_id,
                Message.interaction_run_id,
                Message.outbound_sequence,
            ],
            index_where=(
                Message.direction == MessageDirection.OUT
            )
            & Message.interaction_run_id.is_not(None)
            & Message.outbound_sequence.is_not(None),
        )
        .returning(Message)
    )
    row = await session.scalar(stmt)
    if row is not None:
        return row, True

    existing = await session.scalar(
        select(Message).where(
            Message.contact_id == contact_id,
            Message.direction == MessageDirection.OUT,
            Message.interaction_run_id == interaction_run_id,
            Message.outbound_sequence == sequence,
        )
    )
    if existing is None:  # pragma: no cover - index/transaction invariant
        raise RuntimeError("Interaction outbound reservation disappeared")
    return existing, False


async def update_interaction_outbound_delivery(
    session: AsyncSession,
    *,
    contact_id: int,
    interaction_run_id: uuid.UUID,
    sequence: int,
    delivery_state: MessageDeliveryState,
    provider_message_id: str | None = None,
) -> Message | None:
    """Record one Twilio send result on its already-reserved outbound row."""
    stmt = (
        update(Message)
        .where(
            Message.contact_id == contact_id,
            Message.direction == MessageDirection.OUT,
            Message.interaction_run_id == interaction_run_id,
            Message.outbound_sequence == sequence,
        )
        .values(
            delivery_state=delivery_state,
            provider_message_id=provider_message_id,
        )
        .returning(Message)
    )
    return await session.scalar(stmt, execution_options={"synchronize_session": False})
