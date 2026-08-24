"""Task 044 contracts for the owned Calendar surface and create confirmation."""

from __future__ import annotations

import inspect
import json
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from app.agent import calendar_tools
from app.agent.interaction import (
    INTERACTION_INSTRUCTIONS,
    confirm_event_create,
    pending_action_prompt_summary,
)
from app.agent.owned_tools import build_owned_toolset
from app.database.models import PendingActionKind
from app.integrations.calendar import (
    MAX_EVENTS,
    MAX_RANGE_DAYS,
    CalendarPayloadError,
    create_event_request,
    list_events_request,
    normalize_calendars,
    normalize_event,
    normalize_events,
    resolve_query_window,
    validate_create_payload,
)
from app.integrations.composio_proxy import ProxyResponse, ProxyUnavailable
from app.services import calendar_confirmation
from app.services.calendar_confirmation import _select_event_action, confirm_staged_event


FIXTURES = Path(__file__).parent / "fixtures"
SAO_PAULO = ZoneInfo("America/Sao_Paulo")
NOW = datetime(2026, 8, 15, 22, 30, tzinfo=SAO_PAULO)


def _fixture(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@asynccontextmanager
async def _session_context(session: AsyncMock):
    yield session


def _session_factory(session: AsyncMock) -> MagicMock:
    return MagicMock(side_effect=lambda: _session_context(session))


def _pending(*, kind: PendingActionKind = PendingActionKind.CREATE_EVENT):
    payload = {
        "calendar_id": "primary",
        "title": "Deep work",
        "start": "2026-08-16T14:00:00",
        "end": "2026-08-16T15:00:00",
        "timezone": "America/Sao_Paulo",
        "all_day": False,
        "attendees": ["ana@example.com"],
        "location": "Sala 2",
        "description": "",
    }
    return SimpleNamespace(
        id=uuid.uuid4(),
        kind=kind,
        payload=payload,
        payload_hash=calendar_confirmation.payload_hash(payload),
    )


def test_registry_exposes_only_owned_calendar_reads_and_stage() -> None:
    toolset = build_owned_toolset(active_toolkits=("googlecalendar",))
    assert toolset is not None
    assert set(toolset.tools) == {
        "list_calendars",
        "list_events",
        "get_event",
        "stage_create_event",
    }
    forbidden = {
        "execute_confirmed_event_create",
        "update_event",
        "delete_event",
        "patch_event",
        "GOOGLECALENDAR_CREATE_EVENT",
        "GOOGLECALENDAR_EVENTS_LIST",
        "GOOGLECALENDAR_LIST_CALENDARS",
        "GOOGLECALENDAR_DELETE_EVENT",
    }
    assert set(toolset.tools).isdisjoint(forbidden)


def test_calendar_modules_do_not_import_mcp_or_remote_schemas() -> None:
    import app.agent.calendar_tools as tools
    import app.integrations.calendar as calendar
    import app.services.calendar_confirmation as confirmation

    source = (
        inspect.getsource(calendar)
        + inspect.getsource(tools)
        + inspect.getsource(confirmation)
    )
    assert "composio_mcp" not in source
    assert "MCPToolset" not in source
    assert "GOOGLECALENDAR_" not in source
    assert "PATCH" not in source
    assert "DELETE" not in source


def test_relative_today_uses_contact_clock_and_explicit_provider_timestamps() -> None:
    start, finish = resolve_query_window(
        time_min="hoje", time_max=None, tz=SAO_PAULO, now=NOW
    )
    request = list_events_request(
        calendar_id="primary",
        time_min="today",
        time_max=None,
        max_results=10,
        tz=SAO_PAULO,
        now=NOW,
    )
    params = {item.name: item.value for item in request.parameters}
    assert start.isoformat() == "2026-08-15T00:00:00-03:00"
    assert finish.isoformat() == "2026-08-16T00:00:00-03:00"
    assert params["timeMin"] == "2026-08-15T00:00:00-03:00"
    assert params["timeMax"] == "2026-08-16T00:00:00-03:00"
    assert "hoje" not in params["timeMin"]
    assert params["timeZone"] == "America/Sao_Paulo"
    assert request.endpoint == "/calendars/primary/events"


def test_malformed_provider_datetime_is_skipped_not_fatal() -> None:
    payload = {
        "items": [
            {
                "id": "evt-bad-dt",
                "summary": "Quebrado",
                "status": "confirmed",
                "start": {"dateTime": "not-a-datetime"},
                "end": {"dateTime": "2026-08-16T03:00:00Z"},
            },
            {
                "id": "evt-bad-tz",
                "summary": "Fuso inválido",
                "status": "confirmed",
                "start": {"dateTime": "2026-08-16T14:00:00", "timeZone": "Not/AZone"},
                "end": {"dateTime": "2026-08-16T15:00:00", "timeZone": "Not/AZone"},
            },
            _fixture("calendar_event_timed.json"),
        ]
    }
    events = normalize_events(payload, tz=SAO_PAULO, calendar_id="primary")
    assert [item["event_id"] for item in events] == ["evt-timed-1"]


def test_timezone_boundary_event_renders_in_contact_local_time() -> None:
    events = normalize_events(
        _fixture("calendar_events_mixed.json"),
        tz=SAO_PAULO,
        calendar_id="primary",
    )
    timed = next(item for item in events if item["event_id"] == "evt-timed-1")
    all_day = next(item for item in events if item["event_id"] == "evt-allday-1")
    assert timed["start"] == "2026-08-15T23:00"
    assert timed["end"] == "2026-08-16T00:00"
    assert timed["all_day"] is False
    assert timed["timezone"] == "America/Sao_Paulo"
    assert timed["attendees"] == [{"email": "ana@example.com", "status": "accepted"}]
    assert all_day["start"] == "2026-08-16"
    assert all_day["end"] == "2026-08-17"
    assert all_day["all_day"] is True
    assert all(item["event_id"] != "evt-cancelled-1" for item in events)
    assert all("htmlLink" not in item for item in events)
    assert all("description" not in item for item in events)
    assert all("iCalUID" not in item for item in events)


def test_all_day_get_event_stays_date_only() -> None:
    event = normalize_event(
        _fixture("calendar_event_all_day.json"), tz=SAO_PAULO, calendar_id="primary"
    )
    assert event is not None
    assert event["all_day"] is True
    assert event["start"] == "2026-08-16"
    assert event["end"] == "2026-08-17"
    assert "htmlLink" not in event


def test_hidden_calendars_are_dropped_and_payloads_are_compact() -> None:
    calendars = normalize_calendars(_fixture("calendar_list.json"))
    assert [item["calendar_id"] for item in calendars] == [
        "user@example.com",
        "work@example.com",
    ]
    assert calendars[0]["primary"] is True
    assert all("etag" not in item for item in calendars)


def test_invalid_and_oversized_ranges_are_rejected() -> None:
    with pytest.raises(CalendarPayloadError, match="earlier"):
        resolve_query_window(
            time_min="2026-08-17",
            time_max="2026-08-16",
            tz=SAO_PAULO,
            now=NOW,
        )
    with pytest.raises(CalendarPayloadError, match=str(MAX_RANGE_DAYS)):
        list_events_request(
            calendar_id="primary",
            time_min="2026-08-01",
            time_max="2026-09-16",
            max_results=5,
            tz=SAO_PAULO,
            now=NOW,
        )
    with pytest.raises(CalendarPayloadError, match="max_results"):
        list_events_request(
            calendar_id="primary",
            time_min="today",
            time_max=None,
            max_results=MAX_EVENTS + 1,
            tz=SAO_PAULO,
            now=NOW,
        )


def test_create_payload_resolves_relative_dates_and_validates_fields() -> None:
    payload = validate_create_payload(
        calendar_id="primary",
        title="  Deep work  ",
        start="amanhã",
        end="amanhã",
        timezone="America/Sao_Paulo",
        attendees=["ana@example.com"],
        location="Sala 2",
        description="foco",
        now=NOW,
    )
    assert payload == {
        "calendar_id": "primary",
        "title": "Deep work",
        "start": "2026-08-16",
        "end": "2026-08-17",
        "timezone": "America/Sao_Paulo",
        "all_day": True,
        "attendees": ["ana@example.com"],
        "location": "Sala 2",
        "description": "foco",
    }
    with pytest.raises(CalendarPayloadError, match="attendee"):
        validate_create_payload(
            calendar_id="primary",
            title="Deep work",
            start="2026-08-16T14:00:00",
            end="2026-08-16T15:00:00",
            timezone="America/Sao_Paulo",
            attendees=["not-an-email"],
            location=None,
            description=None,
            now=NOW,
        )


def test_create_request_is_fixed_post_and_never_a_generic_proxy() -> None:
    request = create_event_request(
        {
            "calendar_id": "primary",
            "title": "Deep work",
            "start": "2026-08-16T14:00:00",
            "end": "2026-08-16T15:00:00",
            "timezone": "America/Sao_Paulo",
            "all_day": False,
            "attendees": ["ana@example.com"],
            "location": "Sala 2",
            "description": "",
        }
    )
    assert request.endpoint == "/calendars/primary/events"
    assert request.method == "POST"
    assert request.body == {
        "summary": "Deep work",
        "start": {"dateTime": "2026-08-16T14:00:00", "timeZone": "America/Sao_Paulo"},
        "end": {"dateTime": "2026-08-16T15:00:00", "timeZone": "America/Sao_Paulo"},
        "location": "Sala 2",
        "attendees": [{"email": "ana@example.com"}],
    }
    assert ("sendUpdates", "all") in {(item.name, item.value) for item in request.parameters}


async def test_disconnected_calendar_does_not_call_proxy() -> None:
    ctx = SimpleNamespace(deps=SimpleNamespace(contact_id=7, tz="America/Sao_Paulo"))
    with (
        patch.object(calendar_tools, "_integration", AsyncMock(return_value=None)),
        patch.object(calendar_tools, "_execute", AsyncMock()) as execute,
    ):
        listed = await calendar_tools.list_events(ctx, time_min="today")
        staged = await calendar_tools.stage_create_event(
            ctx, title="Deep work", start="2026-08-16T14:00", end="2026-08-16T15:00"
        )
    assert "não está conectada" in listed
    assert "não está conectada" in staged
    execute.assert_not_awaited()


async def test_list_events_normalizes_without_raw_provider_fields() -> None:
    ctx = SimpleNamespace(deps=SimpleNamespace(contact_id=7, tz="America/Sao_Paulo"))
    execute = AsyncMock(
        return_value=ProxyResponse(200, _fixture("calendar_events_mixed.json"), {})
    )
    with (
        patch.object(calendar_tools, "_integration", AsyncMock(return_value=object())),
        patch.object(calendar_tools, "_execute", execute),
        patch.object(calendar_tools, "_contact_clock", return_value=NOW),
    ):
        result = json.loads(await calendar_tools.list_events(ctx, time_min="hoje"))
    assert [item["event_id"] for item in result["events"]] == ["evt-timed-1", "evt-allday-1"]
    request = execute.await_args.kwargs["request"]
    params = {item.name: item.value for item in request.parameters}
    assert params["timeMin"] == "2026-08-15T00:00:00-03:00"
    dumped = json.dumps(result)
    assert "htmlLink" not in dumped
    assert "SECRET" not in dumped


async def test_calendar_failures_are_logged_without_event_pii() -> None:
    ctx = SimpleNamespace(deps=SimpleNamespace(contact_id=7, tz="America/Sao_Paulo"))
    with (
        patch.object(calendar_tools, "_integration", AsyncMock(return_value=object())),
        patch.object(
            calendar_tools, "_execute", AsyncMock(side_effect=RuntimeError("ana@example.com"))
        ),
        patch.object(calendar_tools.log, "exception") as log_exception,
    ):
        result = await calendar_tools.get_event(ctx, event_id="evt-timed-1")
    assert result == "Não consegui acessar a agenda agora; tente novamente em instantes."
    log_exception.assert_called_once_with(
        "calendar_get_event_failed",
        extra={"event": "calendar_get_event_failed", "contact_id": 7},
    )
    extra = log_exception.call_args.kwargs["extra"]
    assert "ana@example.com" not in str(extra)
    assert "evt-timed-1" not in str(extra)


async def test_stage_create_stores_exact_payload_and_does_not_create() -> None:
    session = AsyncMock()
    execution_id = uuid.uuid4()
    interaction_id = uuid.uuid4()
    ctx = SimpleNamespace(
        deps=SimpleNamespace(
            contact_id=7,
            tz="America/Sao_Paulo",
            execution_run_id=execution_id,
            source_interaction_run_id=interaction_id,
            session_factory=_session_factory(session),
        )
    )
    staged = SimpleNamespace(id=uuid.uuid4())
    with (
        patch.object(calendar_tools, "_integration", AsyncMock(return_value=object())),
        patch.object(calendar_tools, "create_pending_action", AsyncMock(return_value=staged)) as create,
        patch.object(calendar_tools, "_execute", AsyncMock()) as execute,
        patch.object(calendar_tools, "_contact_clock", return_value=NOW),
    ):
        result = json.loads(
            await calendar_tools.stage_create_event(
                ctx,
                title="Deep work",
                start="2026-08-16T14:00:00",
                end="2026-08-16T15:00:00",
                attendees=["ana@example.com"],
                location="Sala 2",
            )
        )
    assert result["status"] == "awaiting_later_confirmation"
    assert create.await_args.kwargs["kind"] == PendingActionKind.CREATE_EVENT
    assert create.await_args.kwargs["payload"] == {
        "calendar_id": "primary",
        "title": "Deep work",
        "start": "2026-08-16T14:00:00",
        "end": "2026-08-16T15:00:00",
        "timezone": "America/Sao_Paulo",
        "all_day": False,
        "attendees": ["ana@example.com"],
        "location": "Sala 2",
        "description": "",
    }
    assert create.await_args.kwargs["source_execution_run_id"] == execution_id
    assert create.await_args.kwargs["source_interaction_run_id"] == interaction_id
    assert create.await_args.kwargs["turn_id"] == str(execution_id)
    execute.assert_not_awaited()


async def test_interaction_reentry_cannot_confirm_and_prompt_blocks_same_turn() -> None:
    assert "criar e" in INTERACTION_INSTRUCTIONS
    assert "confirmar no mesmo turno" in INTERACTION_INSTRUCTIONS
    assert "pode criar" in INTERACTION_INSTRUCTIONS
    ctx = SimpleNamespace(
        deps=SimpleNamespace(
            event_kind="execution_result",
            inbound_turn_id="",
            contact_id=7,
            session_factory=MagicMock(),
        )
    )
    with patch("app.agent.interaction.confirm_staged_event", AsyncMock()) as confirm:
        result = await confirm_event_create(ctx)
    assert "requires a new user message" in result
    confirm.assert_not_awaited()


async def test_confirmation_rejects_wrong_contact_expired_or_duplicate() -> None:
    session = AsyncMock()
    factory = _session_factory(session)
    with patch.object(calendar_confirmation, "list_pending_actions", AsyncMock(return_value=[])):
        outcome = await confirm_staged_event(
            contact_id=999,
            inbound_turn_id="SM-later",
            session_factory=factory,
        )
    assert outcome.state == "none"


async def test_same_turn_claim_cannot_create() -> None:
    session = AsyncMock()
    pending = _pending()
    with (
        patch.object(calendar_confirmation, "list_pending_actions", AsyncMock(return_value=[pending])),
        patch.object(calendar_confirmation, "claim_pending_action", AsyncMock(return_value=None)) as claim,
    ):
        outcome = await confirm_staged_event(
            contact_id=7,
            inbound_turn_id="same-turn",
            session_factory=_session_factory(session),
        )
    assert outcome.state == "none"
    assert claim.await_args.kwargs["turn_id"] == "same-turn"
    assert claim.await_args.kwargs["kind"] == PendingActionKind.CREATE_EVENT


def test_event_confirmation_selects_one_event_among_other_action_kinds() -> None:
    event = _pending()
    email = _pending(kind=PendingActionKind.SEND_EMAIL)
    assert _select_event_action([event, email], action_id=None) == event


def test_event_confirmation_requires_action_id_for_multiple_event_actions() -> None:
    assert _select_event_action([_pending(), _pending()], action_id=None) is None


async def test_confirmation_is_none_when_only_other_action_kinds_are_pending() -> None:
    session = AsyncMock()
    with patch.object(
        calendar_confirmation,
        "list_pending_actions",
        AsyncMock(return_value=[_pending(kind=PendingActionKind.SEND_EMAIL)]),
    ):
        outcome = await confirm_staged_event(
            contact_id=7,
            inbound_turn_id="SM-later",
            session_factory=_session_factory(session),
        )
    assert outcome.state == "none"


async def test_confirmation_is_ambiguous_only_for_multiple_event_actions() -> None:
    session = AsyncMock()
    with patch.object(
        calendar_confirmation,
        "list_pending_actions",
        AsyncMock(return_value=[_pending(), _pending()]),
    ):
        outcome = await confirm_staged_event(
            contact_id=7,
            inbound_turn_id="SM-later",
            session_factory=_session_factory(session),
        )
    assert outcome.state == "ambiguous"


def test_pending_summary_includes_event_title_not_attendees() -> None:
    event = _pending()
    summary = pending_action_prompt_summary([event])
    assert "Deep work" in summary
    assert "create_event" in summary
    assert "ana@example.com" not in summary


async def test_successful_later_create_is_terminal_and_uses_fixed_payload() -> None:
    session = AsyncMock()
    pending = _pending()
    integration = SimpleNamespace(provider="googlecalendar")
    proxy = MagicMock()
    proxy.execute.return_value = ProxyResponse(200, {"id": "evt-created"}, {})
    discard = AsyncMock()
    with (
        patch.object(calendar_confirmation, "list_pending_actions", AsyncMock(return_value=[pending])),
        patch.object(calendar_confirmation, "claim_pending_action", AsyncMock(return_value=pending)),
        patch.object(
            calendar_confirmation, "list_active_integrations", AsyncMock(return_value=[integration])
        ),
        patch.object(calendar_confirmation, "discard_pending_action", discard),
    ):
        outcome = await confirm_staged_event(
            contact_id=7,
            inbound_turn_id="SM-later",
            session_factory=_session_factory(session),
            proxy=proxy,
        )
    assert outcome.state == "created"
    assert outcome.detail == "evento criado; event_id=evt-created"
    request = proxy.execute.call_args.kwargs["request"]
    assert request.endpoint == "/calendars/primary/events"
    assert request.method == "POST"
    assert request.body["summary"] == "Deep work"
    assert proxy.execute.call_args.kwargs["owned_tool_name"] == "execute_confirmed_event_create"
    discard.assert_awaited_once_with(session, action_id=pending.id)


async def test_duplicate_confirmation_cannot_create_a_second_event() -> None:
    session = AsyncMock()
    pending = _pending()
    proxy = MagicMock()
    with (
        patch.object(calendar_confirmation, "list_pending_actions", AsyncMock(return_value=[pending])),
        patch.object(calendar_confirmation, "claim_pending_action", AsyncMock(return_value=None)),
    ):
        outcome = await confirm_staged_event(
            contact_id=7,
            inbound_turn_id="SM-duplicate",
            session_factory=_session_factory(session),
            proxy=proxy,
        )
    assert outcome.state == "none"
    proxy.execute.assert_not_called()


async def test_provider_failure_releases_for_later_retry() -> None:
    session = AsyncMock()
    pending = _pending()
    proxy = MagicMock()
    proxy.execute.side_effect = ProxyUnavailable("down")
    release = AsyncMock()
    with (
        patch.object(calendar_confirmation, "list_pending_actions", AsyncMock(return_value=[pending])),
        patch.object(calendar_confirmation, "claim_pending_action", AsyncMock(return_value=pending)),
        patch.object(
            calendar_confirmation,
            "list_active_integrations",
            AsyncMock(return_value=[SimpleNamespace(provider="googlecalendar")]),
        ),
        patch.object(calendar_confirmation, "release_pending_action", release),
    ):
        outcome = await confirm_staged_event(
            contact_id=7,
            inbound_turn_id="SM-later",
            session_factory=_session_factory(session),
            proxy=proxy,
        )
    assert outcome.state == "retryable_failure"
    release.assert_awaited_once_with(session, action_id=pending.id)
