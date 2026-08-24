"""SQL-level contracts for task 039 persistence helpers."""

from __future__ import annotations

import uuid
from datetime import timedelta
from unittest.mock import AsyncMock

from sqlalchemy.dialects import postgresql

from app.database.models import ExecutionRunStatus, MessageDeliveryState
from app.db.executions import (
    abandon_stale_execution_runs,
    create_or_get_execution_run,
    finish_execution_run,
    mark_execution_event_processed,
    reserve_interaction_outbound,
    update_interaction_outbound_delivery,
)


async def test_event_processing_is_scoped_to_its_contact() -> None:
    session = AsyncMock()
    session.scalar.return_value = None

    await mark_execution_event_processed(
        session, event_id=uuid.uuid4(), contact_id=71
    )

    sql = str(
        session.scalar.await_args.args[0].compile(dialect=postgresql.dialect())
    )
    assert "UPDATE execution_event" in sql
    assert "execution_event.contact_id" in sql


async def test_finish_run_is_scoped_to_contact_and_only_active_states() -> None:
    session = AsyncMock()
    session.scalar.return_value = None

    await finish_execution_run(
        session,
        execution_run_id=uuid.uuid4(),
        contact_id=42,
        status=ExecutionRunStatus.SUCCEEDED,
        result={"summary": "done"},
    )

    sql = str(
        session.scalar.await_args.args[0].compile(dialect=postgresql.dialect())
    )
    assert "UPDATE execution_run" in sql
    assert "execution_run.contact_id" in sql
    assert "execution_run.status IN" in sql


async def test_active_run_dedupe_is_scoped_to_contact() -> None:
    session = AsyncMock()
    existing = object()
    # Contact lock, active dedupe lookup. A matching active run prevents both
    # another capacity count and another insert.
    session.scalar.side_effect = [42, existing]

    row, created = await create_or_get_execution_run(
        session,
        contact_id=42,
        goal="Find recent emails",
        toolkit_scope=["gmail"],
        dedupe_key="gmail:recent-emails",
    )

    assert row is existing
    assert created is False
    dedupe_sql = str(
        session.scalar.await_args_list[1].args[0].compile(
            dialect=postgresql.dialect()
        )
    )
    assert "execution_run.contact_id" in dedupe_sql
    assert "execution_run.dedupe_key" in dedupe_sql
    assert "execution_run.status IN" in dedupe_sql


async def test_outbound_reservation_uses_interaction_sequence_idempotency() -> None:
    session = AsyncMock()
    session.scalar.return_value = object()
    interaction_id = uuid.uuid4()

    _, created = await reserve_interaction_outbound(
        session,
        contact_id=42,
        interaction_run_id=interaction_id,
        sequence=2,
        body="Working on it.",
    )

    assert created is True
    sql = str(
        session.scalar.await_args.args[0].compile(dialect=postgresql.dialect())
    )
    assert "ON CONFLICT (contact_id, interaction_run_id, outbound_sequence)" in sql
    assert "WHERE direction =" in sql
    assert "DO NOTHING" in sql


async def test_outbound_delivery_update_cannot_cross_contact_boundary() -> None:
    session = AsyncMock()
    session.scalar.return_value = None

    await update_interaction_outbound_delivery(
        session,
        contact_id=42,
        interaction_run_id=uuid.uuid4(),
        sequence=1,
        delivery_state=MessageDeliveryState.SENT,
        provider_message_id="SM123",
    )

    sql = str(
        session.scalar.await_args.args[0].compile(dialect=postgresql.dialect())
    )
    assert "UPDATE message" in sql
    assert "message.contact_id" in sql
    assert "message.interaction_run_id" in sql


async def test_stale_running_and_cancel_requested_runs_become_abandoned() -> None:
    session = AsyncMock()
    session.execute.return_value.rowcount = 3

    count = await abandon_stale_execution_runs(
        session, older_than=timedelta(seconds=90)
    )

    assert count == 3
    compiled = session.execute.await_args.args[0].compile(
        dialect=postgresql.dialect()
    )
    sql = str(compiled)
    assert "UPDATE execution_run" in sql
    assert "execution_run.status IN" in sql
    assert ExecutionRunStatus.ABANDONED in compiled.params.values()
