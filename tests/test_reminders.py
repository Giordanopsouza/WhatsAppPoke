"""Reminder tools + 24h window helpers (fixture-style mocks)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.tools import cancel_reminder, list_reminders, set_reminder
from app.database.models import Reminder, ReminderStatus
from app.transport.twilio_wa import in_customer_service_window


def _session_factory(session: AsyncMock) -> async_sessionmaker[AsyncSession]:
    session_cm = AsyncMock()
    session_cm.__aenter__.return_value = session
    session_cm.__aexit__.return_value = None
    factory = MagicMock(return_value=session_cm)
    return cast(async_sessionmaker[AsyncSession], factory)


def _tool_ctx(
    contact_id: int = 7,
    session: AsyncMock | None = None,
    tz: str = "America/Sao_Paulo",
) -> Any:
    sess = session or AsyncMock()
    deps = MagicMock(
        contact_id=contact_id,
        session_factory=_session_factory(sess),
        tz=tz,
        turn_id="job-1",
    )
    return MagicMock(deps=deps)


def _reminder(
    *,
    body: str,
    status: ReminderStatus = ReminderStatus.ACTIVE,
    due_at: datetime | None = None,
) -> Reminder:
    tz = ZoneInfo("America/Sao_Paulo")
    row = MagicMock(spec=Reminder)
    row.id = uuid4()
    row.body = body
    row.status = status
    row.due_at = due_at or datetime(2026, 8, 10, 9, 0, tzinfo=tz)
    return cast(Reminder, row)


def test_in_window_true_within_24h() -> None:
    now = datetime(2026, 8, 9, 12, 0, tzinfo=timezone.utc)
    inbound = now - timedelta(hours=23)
    assert in_customer_service_window(inbound, now=now) is True


def test_in_window_false_outside_24h() -> None:
    now = datetime(2026, 8, 9, 12, 0, tzinfo=timezone.utc)
    inbound = now - timedelta(hours=25)
    assert in_customer_service_window(inbound, now=now) is False


def test_in_window_false_without_inbound() -> None:
    assert in_customer_service_window(None) is False


async def test_set_reminder_inserts_row_and_job_same_commit() -> None:
    session = AsyncMock()
    tz = ZoneInfo("America/Sao_Paulo")
    due = datetime.now(tz) + timedelta(minutes=5)
    created = _reminder(body="alongar", due_at=due)
    ctx = _tool_ctx(session=session)

    with (
        patch("app.agent.tools.create_reminder", new=AsyncMock(return_value=created)) as create,
        patch("app.agent.tools.enqueue_reminder_due", new=AsyncMock()) as enqueue,
    ):
        out = await set_reminder(
            ctx,
            body="  alongar  ",
            due_at=due.strftime("%Y-%m-%dT%H:%M"),
        )

    create.assert_awaited_once()
    assert create.await_args.kwargs["body"] == "alongar"
    enqueue.assert_awaited_once()
    assert enqueue.await_args.kwargs["reminder_id"] == created.id
    assert enqueue.await_args.kwargs["run_at"] == create.await_args.kwargs["due_at"]
    session.commit.assert_awaited_once()
    assert "alongar" in out


async def test_set_reminder_rejects_past_due() -> None:
    ctx = _tool_ctx()
    past = datetime.now(ZoneInfo("America/Sao_Paulo")) - timedelta(minutes=1)
    out = await set_reminder(
        ctx,
        body="x",
        due_at=past.strftime("%Y-%m-%dT%H:%M"),
    )
    assert "passou" in out.casefold()


async def test_list_reminders_active_first() -> None:
    tz = ZoneInfo("America/Sao_Paulo")
    active = _reminder(
        body="Alongar",
        due_at=datetime(2026, 8, 10, 9, 0, tzinfo=tz),
    )
    sent = _reminder(
        body="Já foi",
        status=ReminderStatus.SENT,
        due_at=datetime(2026, 8, 9, 8, 0, tzinfo=tz),
    )
    ctx = _tool_ctx()

    with patch(
        "app.agent.tools.list_reminders_for_contact",
        new=AsyncMock(return_value=[active, sent]),
    ):
        out = await list_reminders(ctx)

    lines = out.splitlines()
    assert lines[1].startswith("1. · Alongar")
    assert lines[2].startswith("2. ✓ Já foi")


async def test_cancel_reminder_by_list_index() -> None:
    a = _reminder(body="Alongar")
    b = _reminder(body="Ligar Ana")
    updated = _reminder(body="Ligar Ana", status=ReminderStatus.CANCELLED)
    updated.id = b.id
    session = AsyncMock()
    ctx = _tool_ctx(session=session)

    with (
        patch(
            "app.agent.tools.list_reminders_for_contact",
            new=AsyncMock(return_value=[a, b]),
        ),
        patch(
            "app.agent.tools.cancel_reminder_row",
            new=AsyncMock(return_value=updated),
        ) as cancel,
    ):
        out = await cancel_reminder(ctx, 2)

    cancel.assert_awaited_once_with(session, reminder_id=b.id)
    assert out == "Lembrete cancelado: Ligar Ana."


async def test_cancel_reminder_rejects_out_of_range() -> None:
    a = _reminder(body="Alongar")
    ctx = _tool_ctx()

    with (
        patch(
            "app.agent.tools.list_reminders_for_contact",
            new=AsyncMock(return_value=[a]),
        ),
        patch("app.agent.tools.cancel_reminder_row", new=AsyncMock()) as cancel,
    ):
        out = await cancel_reminder(ctx, 9)

    cancel.assert_not_awaited()
    assert "Não tem item 9" in out
