"""Task 041 contracts for detached Execution orchestration."""

from __future__ import annotations

import asyncio
import inspect
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.agent.execution import (
    LOCAL_TOOLKIT,
    agent_execution,
    build_execution_toolset,
    build_scoped_execution_toolset,
    ExecutionOutcome,
)
from app.database.models import ExecutionRunStatus
from app.services import execution as execution_service


@asynccontextmanager
async def _session_context(session: AsyncMock):
    yield session


def _session_factory(session: AsyncMock) -> MagicMock:
    return MagicMock(side_effect=lambda: _session_context(session))


def test_execution_agent_is_reusable_non_speaking_and_scoped() -> None:
    source = inspect.getsource(__import__("app.agent.execution", fromlist=["*"]))
    assert agent_execution.name == "agent_execution"
    assert "twilio" not in source.lower()
    assert "send_message_to_user" not in source

    local = build_execution_toolset(toolkit=LOCAL_TOOLKIT)
    assert local is not None
    assert set(local.tools) == {
        "tavily_search",
        "add_task",
        "list_tasks",
        "complete_task",
        "set_reminder",
        "list_reminders",
        "cancel_reminder",
        "create_automation",
        "list_automations",
        "pause_automation",
        "resume_automation",
        "cancel_automation",
    }
    gmail = build_execution_toolset(toolkit="gmail")
    assert gmail is not None
    assert set(gmail.tools) == {
        "search_emails",
        "get_email",
        "create_email_draft",
        "stage_send_email",
    }
    calendar = build_execution_toolset(toolkit="googlecalendar")
    assert calendar is not None
    assert set(calendar.tools) == {
        "list_calendars",
        "list_events",
        "get_event",
        "stage_create_event",
    }
    scoped = build_scoped_execution_toolset(("gmail", "googlecalendar"))
    assert "create_automation" in scoped.tools
    assert "set_reminder" in scoped.tools
    assert "stage_send_email" in scoped.tools
    assert "stage_create_event" in scoped.tools
    assert "execute_confirmed_email_send" not in scoped.tools


async def test_dispatch_starts_once_dedupes_and_reports_busy() -> None:
    session = AsyncMock()
    factory = _session_factory(session)
    first = SimpleNamespace(id=uuid.uuid4())
    second = SimpleNamespace(id=uuid.uuid4())
    run_execution = AsyncMock()
    registry = MagicMock()

    with (
        patch("app.services.execution._toolkits_are_available", AsyncMock(return_value=[])),
        patch(
            "app.services.execution.create_or_get_execution_run",
            AsyncMock(side_effect=[(first, True), (first, False), (None, False)]),
        ),
        patch("app.services.execution._run_execution", run_execution),
        patch("app.services.execution.local_execution_tasks", registry),
    ):
        started = await execution_service.dispatch_execution(
            contact_id=7,
            tz="America/Sao_Paulo",
            goal="buscar notícias",
            toolkits=["local"],
            session_factory=factory,
        )
        deduped = await execution_service.dispatch_execution(
            contact_id=7,
            tz="America/Sao_Paulo",
            goal=" buscar   notícias ",
            toolkits=["local"],
            session_factory=factory,
        )
        busy = await execution_service.dispatch_execution(
            contact_id=7,
            tz="America/Sao_Paulo",
            goal="outro trabalho",
            toolkits=["local"],
            session_factory=factory,
        )
        await asyncio.sleep(0)

    assert started.state == "started"
    assert started.execution_run_id == first.id
    assert deduped.state == "deduped"
    assert deduped.execution_run_id == first.id
    assert busy.state == "busy"
    assert '"state": "busy"' in busy.as_tool_result()
    assert run_execution.await_count == 1
    registry.register.assert_called_once()
    assert session.commit.await_count == 3
    # The persistence helper enforces capacity under the contact row lock.
    assert second.id != first.id


async def test_scheduled_retry_busy_when_another_worker_reclaimed() -> None:
    session = AsyncMock()
    existing = SimpleNamespace(id=uuid.uuid4(), status=ExecutionRunStatus.RUNNING)
    with (
        patch(
            "app.services.execution._toolkits_are_available",
            AsyncMock(return_value=[]),
        ),
        patch(
            "app.services.execution.get_execution_run_by_dedupe",
            AsyncMock(return_value=existing),
        ),
        patch(
            "app.services.execution.reclaim_execution_run_for_retry",
            AsyncMock(return_value=None),
        ),
        patch("app.services.execution._run_execution", AsyncMock()) as run,
    ):
        outcome = await execution_service.run_scheduled_execution(
            contact_id=7,
            tz="America/Sao_Paulo",
            goal="checar a agenda",
            toolkit_scope=("local",),
            dedupe_key="automation:x",
            session_factory=_session_factory(session),
        )

    run.assert_not_awaited()
    assert outcome.state == "busy"
    assert outcome.execution_run_id == existing.id
    assert outcome.detail == "reclaimed_by_another_worker"


async def test_disconnected_toolkit_never_creates_a_run() -> None:
    session = AsyncMock()
    with (
        patch(
            "app.services.execution._toolkits_are_available",
            AsyncMock(return_value=["gmail"]),
        ),
        patch("app.services.execution.create_or_get_execution_run", AsyncMock()) as create,
    ):
        outcome = await execution_service.dispatch_execution(
            contact_id=7,
            tz="America/Sao_Paulo",
            goal="ler inbox",
            toolkits=["gmail"],
            session_factory=_session_factory(session),
        )

    assert outcome.state == "unavailable"
    create.assert_not_awaited()


async def test_dispatch_scopes_one_execution_to_multiple_toolkits() -> None:
    session = AsyncMock()
    factory = _session_factory(session)
    run = SimpleNamespace(id=uuid.uuid4())
    execution = AsyncMock()

    with (
        patch(
            "app.services.execution._toolkits_are_available",
            AsyncMock(return_value=[]),
        ) as available,
        patch(
            "app.services.execution.create_or_get_execution_run",
            AsyncMock(return_value=(run, True)),
        ) as create,
        patch("app.services.execution._run_execution", execution),
        patch("app.services.execution.local_execution_tasks", MagicMock()),
    ):
        outcome = await execution_service.dispatch_execution(
            contact_id=7,
            tz="America/Sao_Paulo",
            goal="encontrar no e-mail e criar o evento",
            toolkits=["googlecalendar", "gmail", "gmail"],
            session_factory=factory,
        )
        await asyncio.sleep(0)

    assert outcome.state == "started"
    assert available.await_args.kwargs["toolkits"] == ("gmail", "googlecalendar")
    assert create.await_args.kwargs["toolkit_scope"] == ["gmail", "googlecalendar"]
    assert execution.await_args.kwargs["toolkit_scope"] == ("gmail", "googlecalendar")


async def test_timeout_finishes_run_and_schedules_reentry() -> None:
    session = AsyncMock()
    finish = AsyncMock()

    async def never_finishes(**_kwargs):
        await asyncio.Future()

    with (
        patch("app.services.execution.claim_execution_run", AsyncMock(return_value=object())),
        patch("app.services.execution.run_execution_goal", never_finishes),
        patch("app.services.execution._finish_and_schedule_reentry", finish),
        patch.object(
            execution_service,
            "settings",
            SimpleNamespace(execution_timeout_seconds=0.001),
        ),
    ):
        await execution_service._run_execution(
            execution_run_id=uuid.uuid4(),
            contact_id=7,
            tz="America/Sao_Paulo",
            goal="pesquisar",
            toolkit="local",
            session_factory=_session_factory(session),
        )

    assert finish.await_args.kwargs["status"] == ExecutionRunStatus.TIMED_OUT


async def test_execution_span_wraps_finish_and_carries_interaction_id() -> None:
    session = AsyncMock()
    source_interaction_run_id = uuid.uuid4()
    span = MagicMock()
    span.__enter__.return_value = span
    span.__exit__.return_value = False

    async def finish_while_span_is_open(**_kwargs):
        assert span.__exit__.call_count == 0

    finish = AsyncMock(side_effect=finish_while_span_is_open)
    with (
        patch("app.services.execution.claim_execution_run", AsyncMock(return_value=object())),
        patch(
            "app.services.execution.run_execution_goal",
            AsyncMock(return_value=ExecutionOutcome(status="succeeded", summary="feito")),
        ),
        patch("app.services.execution._finish_and_schedule_reentry", finish),
        patch("app.services.execution.logfire.span", return_value=span) as make_span,
    ):
        await execution_service._run_execution(
            execution_run_id=uuid.uuid4(),
            contact_id=7,
            tz="America/Sao_Paulo",
            goal="pesquisar",
            toolkit="local",
            session_factory=_session_factory(session),
            source_interaction_run_id=source_interaction_run_id,
        )

    assert make_span.call_args.args == ("execution",)
    assert make_span.call_args.kwargs["source_interaction_run_id"] == str(
        source_interaction_run_id
    )
    span.set_attribute.assert_any_call("execution.status", "succeeded")
    assert finish.await_args.kwargs["status"] == ExecutionRunStatus.SUCCEEDED
    assert finish.await_args.kwargs["source_interaction_run_id"] == source_interaction_run_id
    assert finish.await_args.kwargs["result"]["summary"] == "feito"
    assert finish.await_args.kwargs["result"]["outcome"] == "succeeded"


async def test_failed_outcome_finishes_run_as_failed() -> None:
    session = AsyncMock()
    finish = AsyncMock()

    with (
        patch("app.services.execution.claim_execution_run", AsyncMock(return_value=object())),
        patch(
            "app.services.execution.run_execution_goal",
            AsyncMock(
                return_value=ExecutionOutcome(
                    status="needs_input",
                    summary="assunto vazio; informe um assunto",
                )
            ),
        ),
        patch("app.services.execution._finish_and_schedule_reentry", finish),
    ):
        await execution_service._run_execution(
            execution_run_id=uuid.uuid4(),
            contact_id=7,
            tz="America/Sao_Paulo",
            goal="enviar e-mail",
            toolkit="local",
            session_factory=_session_factory(session),
        )

    assert finish.await_args.kwargs["status"] == ExecutionRunStatus.FAILED
    assert finish.await_args.kwargs["result"]["outcome"] == "needs_input"
    assert finish.await_args.kwargs.get("error") is None


async def test_cancel_race_marks_terminal_even_if_task_never_started() -> None:
    session = AsyncMock()
    run_id = uuid.uuid4()
    row = SimpleNamespace(goal="pesquisar")
    registry = MagicMock()
    finish = AsyncMock()

    with (
        patch("app.services.execution.request_execution_cancellation", AsyncMock(return_value=row)),
        patch("app.services.execution.local_execution_tasks", registry),
        patch("app.services.execution._finish_and_schedule_reentry", finish),
    ):
        outcome = await execution_service.cancel_execution(
            contact_id=7,
            execution_run_id=run_id,
            session_factory=_session_factory(session),
        )
        await asyncio.sleep(0)

    assert outcome.state == "cancelled"
    registry.cancel.assert_called_once_with(run_id)
    assert finish.await_args.kwargs["status"] == ExecutionRunStatus.CANCELLED


async def test_reentry_uses_interaction_with_event_context_not_stale_history() -> None:
    session = AsyncMock()
    session.get.return_value = SimpleNamespace(phone="15551234567")
    reenter = AsyncMock()
    mark = AsyncMock()
    event_id = uuid.uuid4()
    run_id = uuid.uuid4()

    with (
        patch("app.agent.interaction.run_interaction_event", reenter),
        patch("app.services.execution.mark_execution_event_processed", mark),
    ):
        await execution_service._reenter_interaction(
            event_id=event_id,
            execution_run_id=run_id,
            contact_id=7,
            goal="ver inbox",
            status=ExecutionRunStatus.SUCCEEDED,
            result={"summary": "nada urgente"},
            error=None,
            session_factory=_session_factory(session),
        )

    kwargs = reenter.await_args.kwargs
    assert kwargs["contact_id"] == 7
    assert kwargs["provider_message_id"] == f"execution:{run_id}"
    assert kwargs["event_kind"] == "execution_result"
    assert "ver inbox" in kwargs["internal_event_summary"]
    assert "nada urgente" in kwargs["internal_event_summary"]
    # run_interaction_event acquires the Interaction lock and loads history at
    # call time, so it cannot receive an execution-time history snapshot.
    assert "history" not in kwargs
    mark.assert_awaited_once_with(session, event_id=event_id, contact_id=7)


async def test_cleanup_abandons_without_reentry() -> None:
    session = AsyncMock()
    abandon = AsyncMock(return_value=2)
    with patch("app.services.execution.abandon_stale_execution_runs", abandon):
        count = await execution_service.abandon_expired_executions(
            session_factory=_session_factory(session)
        )

    assert count == 2
    abandon.assert_awaited_once()
    assert session.commit.await_count == 1


async def test_send_goal_requires_staged_pending_action() -> None:
    execution_id = uuid.uuid4()
    session = AsyncMock()
    session.scalar = AsyncMock(return_value=None)
    factory = _session_factory(session)
    output = await execution_service._validate_email_send_staging(
        goal="Enviar um email para ana@example.com",
        toolkit_scope=("gmail",),
        output=ExecutionOutcome(status="succeeded", summary="Rascunho criado."),
        contact_id=7,
        execution_run_id=execution_id,
        session_factory=factory,
    )
    assert output.status == "failed"
    assert "confirmação" in output.summary


async def test_send_goal_passes_when_pending_action_exists() -> None:
    execution_id = uuid.uuid4()
    session = AsyncMock()
    session.scalar = AsyncMock(return_value=uuid.uuid4())
    factory = _session_factory(session)
    original = ExecutionOutcome(status="succeeded", summary="Encenado.")
    output = await execution_service._validate_email_send_staging(
        goal="Enviar um email para ana@example.com",
        toolkit_scope=("gmail",),
        output=original,
        contact_id=7,
        execution_run_id=execution_id,
        session_factory=factory,
    )
    assert output is original
