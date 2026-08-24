"""Task 045 contracts: RRULE automations, catch-up, jobs, and reminder split."""

from __future__ import annotations

import inspect
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.automation_tools import (
    cancel_automation,
    create_automation,
    list_automations,
    pause_automation,
    resume_automation,
)
from app.agent.gmail_tools import stage_send_email
from app.core.rrule import (
    CATCH_UP_GRACE,
    RRuleError,
    canonicalize_rrule,
    is_catch_up,
    next_occurrence_utc,
)
from app.database.models import (
    Automation,
    AutomationLastRunStatus,
    AutomationStatus,
    JobKind,
)
from app.db.automations import (
    get_automation,
    record_automation_run,
    set_automation_status,
    upsert_automation_due,
)
from app.services.automation import fire_due_automation
from app.services.execution import DispatchOutcome
from app.worker.handlers import automation_due, reminder_due
from app.worker import loop as worker_loop


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
        execution_run_id=uuid.uuid4(),
    )
    return MagicMock(deps=deps)


def _automation(
    *,
    name: str = "Agenda",
    status: AutomationStatus = AutomationStatus.ACTIVE,
    rrule: str = "FREQ=WEEKLY;INTERVAL=1;BYDAY=MO,TU,WE,TH,FR;BYHOUR=8;BYMINUTE=0;BYSECOND=0",
    next_run_at: datetime | None = None,
    last_occurrence_at: datetime | None = None,
    last_run_status: AutomationLastRunStatus | None = None,
    required_toolkits: list[str] | None = None,
    timezone_name: str = "America/Sao_Paulo",
) -> Automation:
    row = MagicMock(spec=Automation)
    row.id = uuid.uuid4()
    row.contact_id = 7
    row.name = name
    row.goal = "checar a agenda"
    row.rrule = rrule
    row.timezone = timezone_name
    row.required_toolkits = required_toolkits or []
    row.status = status
    row.next_run_at = next_run_at
    row.last_occurrence_at = last_occurrence_at
    row.last_run_status = last_run_status
    row.last_execution_run_id = None
    row.created_at = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
    return cast(Automation, row)


def test_rrule_rejects_invalid_and_unbounded_inputs() -> None:
    canonicalize_rrule("FREQ=DAILY;BYHOUR=8;BYMINUTE=0")
    canonicalize_rrule("FREQ=HOURLY;INTERVAL=2")
    for raw in (
        "",
        "FREQ=SECONDLY",
        "FREQ=MINUTELY;INTERVAL=5",
        "DTSTART=20260815T080000;FREQ=DAILY",
        "FREQ=DAILY;COUNT=0",
        "FREQ=DAILY;COUNT=9999",
        "INTERVAL=1",
        "FREQ=DAILY;INTERVAL=0",
    ):
        try:
            canonicalize_rrule(raw)
        except RRuleError:
            continue
        raise AssertionError(f"expected RRuleError for {raw!r}")


def test_weekday_rrule_resolves_in_contact_timezone_to_utc() -> None:
    tz = ZoneInfo("America/Sao_Paulo")
    after = datetime(2026, 8, 14, 9, 0, tzinfo=tz)  # Friday
    nxt = next_occurrence_utc(
        rrule="FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR;BYHOUR=8;BYMINUTE=0",
        timezone_name="America/Sao_Paulo",
        after=after,
        dtstart=datetime(2026, 8, 1, 8, 0, tzinfo=tz),
    )
    assert nxt is not None
    local = nxt.astimezone(tz)
    assert local.weekday() == 0  # Monday
    assert local.hour == 8
    assert local.minute == 0
    assert nxt.tzinfo == timezone.utc


def test_yearly_interval_and_leap_day_are_not_cut_off() -> None:
    tz = ZoneInfo("America/Sao_Paulo")
    after = datetime(2026, 8, 15, 9, 0, tzinfo=tz)
    start = datetime(2026, 8, 15, 8, 0, tzinfo=tz)
    biennial = next_occurrence_utc(
        rrule="FREQ=YEARLY;INTERVAL=2;BYMONTH=8;BYMONTHDAY=15;BYHOUR=8;BYMINUTE=0",
        timezone_name="America/Sao_Paulo",
        after=after,
        dtstart=start,
    )
    assert biennial is not None
    assert biennial.astimezone(tz).year == 2028
    triennial = next_occurrence_utc(
        rrule="FREQ=YEARLY;INTERVAL=3;BYMONTH=8;BYMONTHDAY=15;BYHOUR=8;BYMINUTE=0",
        timezone_name="America/Sao_Paulo",
        after=after,
        dtstart=start,
    )
    assert triennial is not None
    assert triennial.astimezone(tz).year == 2029
    leap_after = datetime(2024, 3, 1, 0, 0, tzinfo=tz)
    leap = next_occurrence_utc(
        rrule="FREQ=YEARLY;BYMONTH=2;BYMONTHDAY=29;BYHOUR=8;BYMINUTE=0",
        timezone_name="America/Sao_Paulo",
        after=leap_after,
        dtstart=datetime(2024, 2, 29, 8, 0, tzinfo=tz),
    )
    assert leap is not None
    local = leap.astimezone(tz)
    assert (local.year, local.month, local.day) == (2028, 2, 29)


def test_monthly_bymonthday_skips_short_months() -> None:
    tz = ZoneInfo("America/Sao_Paulo")
    after = datetime(2026, 1, 31, 9, 0, tzinfo=tz)
    nxt = next_occurrence_utc(
        rrule="FREQ=MONTHLY;BYMONTHDAY=31;BYHOUR=9;BYMINUTE=0",
        timezone_name="America/Sao_Paulo",
        after=after,
        dtstart=datetime(2026, 1, 31, 9, 0, tzinfo=tz),
    )
    assert nxt is not None
    local = nxt.astimezone(tz)
    assert (local.month, local.day, local.hour) == (3, 31, 9)


def test_daily_rrule_keeps_wall_clock_across_dst() -> None:
    tz = ZoneInfo("America/New_York")
    before = next_occurrence_utc(
        rrule="FREQ=DAILY;BYHOUR=9;BYMINUTE=0",
        timezone_name="America/New_York",
        after=datetime(2026, 3, 7, 8, 0, tzinfo=tz),
        dtstart=datetime(2026, 3, 1, 9, 0, tzinfo=tz),
    )
    after = next_occurrence_utc(
        rrule="FREQ=DAILY;BYHOUR=9;BYMINUTE=0",
        timezone_name="America/New_York",
        after=datetime(2026, 3, 8, 12, 0, tzinfo=tz),
        dtstart=datetime(2026, 3, 1, 9, 0, tzinfo=tz),
    )
    assert before is not None and after is not None
    assert before.astimezone(tz).hour == 9
    assert after.astimezone(tz).hour == 9
    assert before.astimezone(tz).utcoffset() != after.astimezone(tz).utcoffset()


def test_catch_up_advances_to_next_future_not_every_missed_slot() -> None:
    tz = ZoneInfo("America/Sao_Paulo")
    scheduled = datetime(2026, 8, 10, 8, 0, tzinfo=tz)  # Monday
    now = datetime(2026, 8, 13, 10, 0, tzinfo=tz)  # Thursday
    assert is_catch_up(scheduled_at=scheduled, now=now) is True
    nxt = next_occurrence_utc(
        rrule="FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR;BYHOUR=8;BYMINUTE=0",
        timezone_name="America/Sao_Paulo",
        after=now,
        dtstart=datetime(2026, 8, 1, 8, 0, tzinfo=tz),
    )
    assert nxt is not None
    local = nxt.astimezone(tz)
    assert local.date().isoformat() == "2026-08-14"
    assert local.hour == 8
    on_time = scheduled + CATCH_UP_GRACE
    assert is_catch_up(scheduled_at=scheduled, now=on_time) is False


async def test_create_automation_inserts_row_and_due_job() -> None:
    session = AsyncMock()
    ctx = _tool_ctx(session=session)
    created = _automation(
        next_run_at=datetime(2026, 8, 17, 11, 0, tzinfo=timezone.utc)
    )
    with (
        patch(
            "app.agent.automation_tools.next_occurrence_utc",
            return_value=created.next_run_at,
        ),
        patch(
            "app.agent.automation_tools.create_automation_row",
            AsyncMock(return_value=created),
        ) as create,
        patch(
            "app.agent.automation_tools.upsert_automation_due",
            AsyncMock(return_value=True),
        ) as enqueue,
    ):
        out = await create_automation(
            ctx,
            name=" Agenda ",
            goal="checar a agenda",
            rrule="FREQ=WEEKLY;BYDAY=MO;BYHOUR=8;BYMINUTE=0",
            required_toolkits="googlecalendar",
        )

    create.assert_awaited_once()
    assert create.await_args.kwargs["required_toolkits"] == ["googlecalendar"]
    enqueue.assert_awaited_once()
    assert enqueue.await_args.kwargs["automation_id"] == created.id
    assert enqueue.await_args.kwargs["run_at"] == created.next_run_at
    session.commit.assert_awaited_once()
    assert "Agenda" in out


async def test_create_automation_rejects_bad_rrule_and_toolkit() -> None:
    ctx = _tool_ctx()
    bad = await create_automation(ctx, name="x", goal="y", rrule="FREQ=SECONDLY")
    assert "RRULE inválida" in bad
    unknown = await create_automation(
        ctx,
        name="x",
        goal="y",
        rrule="FREQ=DAILY;BYHOUR=8;BYMINUTE=0",
        required_toolkits="notion",
    )
    assert "não suportado" in unknown.casefold()


async def test_list_pause_resume_cancel_automations() -> None:
    active = _automation(name="Ativa")
    paused = _automation(name="Pausada", status=AutomationStatus.PAUSED)
    session = AsyncMock()
    ctx = _tool_ctx(session=session)

    with patch(
        "app.agent.automation_tools.list_automations_for_contact",
        AsyncMock(return_value=[active, paused]),
    ):
        listed = await list_automations(ctx)
    assert "1. · Ativa" in listed
    assert "2. ⏸ Pausada" in listed

    paused_row = _automation(name="Ativa", status=AutomationStatus.PAUSED)
    with (
        patch(
            "app.agent.automation_tools.list_automations_for_contact",
            AsyncMock(return_value=[active, paused]),
        ),
        patch(
            "app.agent.automation_tools.set_automation_status",
            AsyncMock(return_value=paused_row),
        ) as set_status,
    ):
        out = await pause_automation(ctx, 1)
    set_status.assert_awaited_once()
    assert set_status.await_args.kwargs["status"] == AutomationStatus.PAUSED
    assert "pausada" in out.casefold()

    next_run = datetime(2026, 8, 18, 11, 0, tzinfo=timezone.utc)
    resumed = _automation(name="Pausada", next_run_at=next_run)
    with (
        patch(
            "app.agent.automation_tools.list_automations_for_contact",
            AsyncMock(return_value=[active, paused]),
        ),
        patch(
            "app.agent.automation_tools.next_occurrence_utc",
            return_value=next_run,
        ),
        patch(
            "app.agent.automation_tools.set_automation_status",
            AsyncMock(return_value=resumed),
        ),
        patch(
            "app.agent.automation_tools.upsert_automation_due",
            AsyncMock(return_value=True),
        ) as enqueue,
    ):
        out = await resume_automation(ctx, 2)
    enqueue.assert_awaited_once()
    assert "retomada" in out.casefold()

    cancelled = _automation(name="Ativa", status=AutomationStatus.CANCELLED)
    with (
        patch(
            "app.agent.automation_tools.list_automations_for_contact",
            AsyncMock(return_value=[active, paused]),
        ),
        patch(
            "app.agent.automation_tools.set_automation_status",
            AsyncMock(return_value=cancelled),
        ),
    ):
        out = await cancel_automation(ctx, 1)
    assert "cancelada" in out.casefold()


@asynccontextmanager
async def _session_context(session: AsyncMock):
    yield session


def _job_for(automation: Automation) -> MagicMock:
    job = MagicMock()
    job.id = uuid.uuid4()
    job.contact_id = automation.contact_id
    job.kind = JobKind.AUTOMATION_DUE
    job.payload = {
        "automation_id": str(automation.id),
        "occurrence_at": (
            automation.next_run_at or datetime.now(timezone.utc)
        ).isoformat(),
    }
    return job


async def test_paused_or_cancelled_automation_is_noop() -> None:
    paused = _automation(status=AutomationStatus.PAUSED)
    job = _job_for(paused)
    session = AsyncMock()
    session.get.return_value = SimpleNamespace(phone="5511999", tz="America/Sao_Paulo")

    with (
        patch("app.services.automation.get_session", lambda: _session_context(session)),
        patch("app.services.automation.get_automation", AsyncMock(return_value=paused)),
        patch("app.services.automation.run_scheduled_execution", AsyncMock()) as run,
    ):
        await fire_due_automation(job)
    run.assert_not_awaited()


async def test_duplicate_wake_up_does_not_start_a_second_execution() -> None:
    occurrence = datetime(2026, 8, 17, 11, 0, tzinfo=timezone.utc)
    row = _automation(
        next_run_at=occurrence + timedelta(days=1),
        last_occurrence_at=occurrence,
        last_run_status=AutomationLastRunStatus.SUCCEEDED,
    )
    job = _job_for(row)
    job.payload["occurrence_at"] = occurrence.isoformat()
    session = AsyncMock()
    session.get.return_value = SimpleNamespace(phone="5511999", tz="America/Sao_Paulo")

    with (
        patch("app.services.automation.get_session", lambda: _session_context(session)),
        patch("app.services.automation.get_automation", AsyncMock(return_value=row)),
        patch("app.services.automation.complete_job", AsyncMock()) as complete,
        patch("app.services.automation.upsert_automation_due", AsyncMock()) as enqueue,
        patch("app.services.automation.run_scheduled_execution", AsyncMock()) as run,
    ):
        await fire_due_automation(job)

    run.assert_not_awaited()
    complete.assert_awaited_once()
    enqueue.assert_awaited_once()


async def test_disconnected_required_app_skips_and_reenters() -> None:
    occurrence = datetime(2026, 8, 17, 11, 0, tzinfo=timezone.utc)
    row = _automation(
        next_run_at=occurrence,
        required_toolkits=["gmail"],
    )
    job = _job_for(row)
    session = AsyncMock()
    session.get.return_value = SimpleNamespace(phone="5511999", tz="America/Sao_Paulo")
    reenter = AsyncMock()

    with (
        patch("app.services.automation.get_session", lambda: _session_context(session)),
        patch("app.services.automation.get_automation", AsyncMock(return_value=row)),
        patch(
            "app.services.automation.run_scheduled_execution",
            AsyncMock(
                return_value=DispatchOutcome(
                    "unavailable", detail="required toolkit is not connected: gmail"
                )
            ),
        ) as run,
        patch("app.agent.interaction.run_interaction_event", reenter),
        patch("app.services.automation.record_automation_run", AsyncMock(return_value=row)),
        patch("app.services.automation.complete_job", AsyncMock()),
        patch("app.services.automation.upsert_automation_due", AsyncMock()),
        patch(
            "app.services.automation.next_occurrence_utc",
            return_value=occurrence + timedelta(days=1),
        ),
    ):
        await fire_due_automation(job)

    run.assert_awaited_once()
    reenter.assert_awaited_once()
    assert "skipped" in reenter.await_args.kwargs["internal_event_summary"]


async def test_success_runs_execution_once_then_advances() -> None:
    occurrence = datetime(2026, 8, 10, 11, 0, tzinfo=timezone.utc)
    row = _automation(next_run_at=occurrence, required_toolkits=["googlecalendar"])
    job = _job_for(row)
    session = AsyncMock()
    session.get.return_value = SimpleNamespace(phone="5511999", tz="America/Sao_Paulo")
    run_id = uuid.uuid4()
    future = occurrence + timedelta(days=1)

    with (
        patch("app.services.automation.get_session", lambda: _session_context(session)),
        patch("app.services.automation.get_automation", AsyncMock(return_value=row)),
        patch(
            "app.services.automation.run_scheduled_execution",
            AsyncMock(
                return_value=DispatchOutcome(
                    "started", execution_run_id=run_id, detail="succeeded"
                )
            ),
        ) as run,
        patch("app.services.automation.record_automation_run", AsyncMock(return_value=row)) as record,
        patch("app.services.automation.complete_job", AsyncMock()),
        patch("app.services.automation.upsert_automation_due", AsyncMock()) as enqueue,
        patch("app.services.automation.next_occurrence_utc", return_value=future),
        patch("app.services.automation.is_catch_up", return_value=True),
    ):
        await fire_due_automation(job)

    run.assert_awaited_once()
    assert record.await_args.kwargs["was_catch_up"] is True
    assert record.await_args.kwargs["execution_run_id"] == run_id
    assert record.await_args.kwargs["last_run_status"] == AutomationLastRunStatus.SUCCEEDED
    assert enqueue.await_args.kwargs["run_at"] == future


async def test_busy_execution_retries_without_closing_the_occurrence() -> None:
    row = _automation(next_run_at=datetime.now(timezone.utc))
    job = _job_for(row)
    session = AsyncMock()
    session.get.return_value = SimpleNamespace(phone="5511999", tz="America/Sao_Paulo")

    with (
        patch("app.services.automation.get_session", lambda: _session_context(session)),
        patch("app.services.automation.get_automation", AsyncMock(return_value=row)),
        patch(
            "app.services.automation.run_scheduled_execution",
            AsyncMock(return_value=DispatchOutcome("busy", detail="full")),
        ),
        patch("app.services.automation.record_automation_run", AsyncMock()) as record,
    ):
        try:
            await fire_due_automation(job)
        except RuntimeError as exc:
            assert "capacity" in str(exc)
        else:
            raise AssertionError("expected retryable busy error")
    record.assert_not_awaited()


async def test_get_automation_is_contact_scoped() -> None:
    session = AsyncMock()
    session.scalar.return_value = None
    await get_automation(session, automation_id=uuid.uuid4(), contact_id=71)
    sql = str(session.scalar.await_args.args[0].compile(dialect=postgresql.dialect()))
    assert "automation.contact_id" in sql


async def test_cancel_clears_next_run_at() -> None:
    session = AsyncMock()
    session.scalar = AsyncMock(return_value=None)
    await set_automation_status(
        session,
        automation_id=uuid.uuid4(),
        contact_id=7,
        status=AutomationStatus.CANCELLED,
    )
    compiled = session.scalar.await_args.args[0].compile(dialect=postgresql.dialect())
    assert compiled.params["next_run_at"] is None


async def test_record_automation_run_only_stamps_active_rows() -> None:
    session = AsyncMock()
    session.scalar = AsyncMock(return_value=None)
    await record_automation_run(
        session,
        automation_id=uuid.uuid4(),
        contact_id=7,
        occurrence_at=datetime(2026, 8, 17, 11, 0, tzinfo=timezone.utc),
        next_run_at=datetime(2026, 8, 18, 11, 0, tzinfo=timezone.utc),
        last_run_status=AutomationLastRunStatus.SUCCEEDED,
        execution_run_id=None,
        was_catch_up=False,
    )
    compiled = session.scalar.await_args.args[0].compile(dialect=postgresql.dialect())
    sql = str(compiled)
    assert "automation.status" in sql
    assert compiled.params["status_1"] == AutomationStatus.ACTIVE


async def test_upsert_retries_insert_when_pending_job_vanishes() -> None:
    session = MagicMock()

    class _Nested:
        async def __aenter__(self) -> "_Nested":
            return self

        async def __aexit__(self, *args: object) -> bool:
            return False

    session.begin_nested.return_value = _Nested()
    session.scalar = AsyncMock(return_value=None)
    inserts = {"n": 0}

    async def execute(_stmt: object) -> None:
        inserts["n"] += 1
        if inserts["n"] == 1:
            raise IntegrityError("INSERT", {}, Exception("unique"))

    session.execute = AsyncMock(side_effect=execute)
    inserted = await upsert_automation_due(
        session,
        contact_id=7,
        automation_id=uuid.uuid4(),
        run_at=datetime(2026, 8, 17, 11, 0, tzinfo=timezone.utc),
    )
    assert inserted is True
    assert inserts["n"] == 2


async def test_upsert_automation_due_payload_includes_automation_id() -> None:
    session = MagicMock()

    class _Nested:
        async def __aenter__(self) -> "_Nested":
            return self

        async def __aexit__(self, *args: object) -> bool:
            return False

    session.begin_nested.return_value = _Nested()
    session.execute = AsyncMock()
    session.scalar = AsyncMock(return_value=None)
    run_at = datetime(2026, 8, 17, 11, 0, tzinfo=timezone.utc)
    inserted = await upsert_automation_due(
        session, contact_id=7, automation_id=uuid.uuid4(), run_at=run_at
    )
    assert inserted is True
    compiled = session.execute.await_args.args[0].compile(dialect=postgresql.dialect())
    sql = str(compiled)
    assert "INSERT INTO job" in sql
    assert "automation_due" in sql or "automation_due" in compiled.params.values()


def test_architecture_boundaries() -> None:
    fire_src = inspect.getsource(fire_due_automation)
    handler_src = inspect.getsource(automation_due)
    reminder_src = inspect.getsource(reminder_due)
    assert "send_text" not in fire_src
    assert "send_content_template" not in fire_src
    assert "send_briefing_template" not in fire_src
    assert "send_text" not in handler_src
    assert "run_scheduled_execution" in fire_src
    assert "run_interaction_event" in inspect.getsource(
        inspect.getmodule(fire_due_automation)
    )
    assert "fire_due_automation" not in reminder_src
    assert JobKind.AUTOMATION_DUE in worker_loop.HANDLERS
    assert worker_loop.HANDLERS[JobKind.AUTOMATION_DUE] is automation_due.handle_automation_due


def test_sensitive_writes_still_stage_pending_action() -> None:
    source = inspect.getsource(stage_send_email)
    assert "create_pending_action" in source
    assert "PendingActionKind.SEND_EMAIL" in source
    fire_src = inspect.getsource(fire_due_automation)
    assert "pre-confirm" not in fire_src
    assert "claimed" not in fire_src


def test_reminder_tools_are_not_automation_jobs() -> None:
    from app.agent.tools import set_reminder

    source = inspect.getsource(set_reminder)
    assert "enqueue_reminder_due" in source
    assert "upsert_automation_due" not in source
    create_src = inspect.getsource(create_automation)
    assert "upsert_automation_due" in create_src
    assert "enqueue_reminder_due" not in create_src
