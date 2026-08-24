"""Owned, typed Calendar tools exposed only to Calendar-scoped Execution runs."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Any

from pydantic_ai import RunContext

from app.core.logutil import get_logger
from app.core.timeutil import resolve_tz
from app.database.models import PendingActionKind
from app.db import create_pending_action, list_active_integrations
from app.integrations.calendar import (
    CalendarPayloadError,
    event_request,
    list_calendars_request,
    list_events_request,
    normalize_calendars,
    normalize_event,
    normalize_events,
    validate_create_payload,
)
from app.integrations.composio_proxy import AuthenticatedProxyAdapter, ProxyRequest


_proxy = AuthenticatedProxyAdapter(toolkit="googlecalendar")
log = get_logger(__name__)
_UNAVAILABLE = "Agenda não está conectada ou disponível para esta pessoa."
_FAILED = "Não consegui acessar a agenda agora; tente novamente em instantes."


# Check if this contact has Google Calendar connected.
async def _integration(ctx: RunContext[Any]) -> Any | None:
    async with ctx.deps.session_factory() as session:
        rows = await list_active_integrations(session, contact_id=ctx.deps.contact_id)
    return next((row for row in rows if row.provider == "googlecalendar"), None)


# Call Composio's authenticated proxy for a calendar API request.
async def _execute(
    ctx: RunContext[Any], *, tool_name: str, request: ProxyRequest, integration: Any
):
    return await asyncio.to_thread(
        _proxy.execute,
        contact_id=ctx.deps.contact_id,
        integration=integration,
        owned_tool_name=tool_name,
        request=request,
    )


# Current date/time in the contact's timezone.
def _contact_clock(ctx: RunContext[Any]) -> datetime:
    return datetime.now(resolve_tz(ctx.deps.tz))


async def list_calendars(ctx: RunContext[Any]) -> str:
    """List calendars the person can read. Returns ids, titles, and access only."""
    integration = await _integration(ctx)
    if integration is None:
        return _UNAVAILABLE
    try:
        response = await _execute(
            ctx,
            tool_name="list_calendars",
            request=list_calendars_request(),
            integration=integration,
        )
        return json.dumps(
            {"calendars": normalize_calendars(response.data)},
            ensure_ascii=False,
        )
    except Exception:
        log.exception(
            "calendar_list_calendars_failed",
            extra={"event": "calendar_list_calendars_failed", "contact_id": ctx.deps.contact_id},
        )
        return _FAILED


async def list_events(
    ctx: RunContext[Any],
    calendar_id: str = "primary",
    time_min: str | None = None,
    time_max: str | None = None,
    max_results: int = 10,
) -> str:
    """List events in a bounded local window. Never returns raw provider payloads.

    time_min/time_max accept today/tomorrow/yesterday/this_week/next_week (or
    hoje/amanhã/ontem/esta_semana/próxima_semana) or YYYY-MM-DD / ISO datetime.
    Relative tokens are resolved from the contact clock before the provider call.
    """
    tz = resolve_tz(ctx.deps.tz)
    try:
        request = list_events_request(
            calendar_id=calendar_id,
            time_min=time_min,
            time_max=time_max,
            max_results=max_results,
            tz=tz,
            now=_contact_clock(ctx),
        )
    except CalendarPayloadError as exc:
        return f"Busca inválida: {exc}."
    integration = await _integration(ctx)
    if integration is None:
        return _UNAVAILABLE
    try:
        response = await _execute(
            ctx, tool_name="list_events", request=request, integration=integration
        )
        events = normalize_events(response.data, tz=tz, calendar_id=calendar_id)
        return json.dumps({"events": events}, ensure_ascii=False)
    except Exception:
        log.exception(
            "calendar_list_events_failed",
            extra={"event": "calendar_list_events_failed", "contact_id": ctx.deps.contact_id},
        )
        return _FAILED


async def get_event(
    ctx: RunContext[Any], event_id: str, calendar_id: str = "primary"
) -> str:
    """Fetch one event with normalized ids, local times, attendees, and status."""
    tz = resolve_tz(ctx.deps.tz)
    try:
        request = event_request(calendar_id=calendar_id, event_id=event_id, tz=tz)
    except CalendarPayloadError as exc:
        return f"Evento inválido: {exc}."
    integration = await _integration(ctx)
    if integration is None:
        return _UNAVAILABLE
    try:
        response = await _execute(
            ctx, tool_name="get_event", request=request, integration=integration
        )
        event = normalize_event(response.data, tz=tz, calendar_id=calendar_id)
        if event is None:
            return "Evento não encontrado. Use um event_id retornado pela listagem."
        return json.dumps(event, ensure_ascii=False)
    except Exception:
        log.exception(
            "calendar_get_event_failed",
            extra={"event": "calendar_get_event_failed", "contact_id": ctx.deps.contact_id},
        )
        return _FAILED


async def stage_create_event(
    ctx: RunContext[Any],
    title: str,
    start: str,
    end: str,
    calendar_id: str = "primary",
    timezone: str | None = None,
    attendees: list[str] | None = None,
    location: str | None = None,
    description: str | None = None,
) -> str:
    """Stage an exact event create for later explicit WhatsApp confirmation.

    This function never creates the event. A request to create and confirm in
    this same user turn still stops here and asks for a later confirmation.
    start/end accept YYYY-MM-DD (all-day), ISO datetime, or today/tomorrow.
    """
    try:
        payload = validate_create_payload(
            calendar_id=calendar_id,
            title=title,
            start=start,
            end=end,
            timezone=timezone or ctx.deps.tz,
            attendees=attendees,
            location=location,
            description=description,
            now=_contact_clock(ctx),
        )
    except CalendarPayloadError as exc:
        return f"Evento inválido: {exc}."
    if await _integration(ctx) is None:
        return _UNAVAILABLE

    execution_run_id = getattr(ctx.deps, "execution_run_id", None)
    async with ctx.deps.session_factory() as session:
        row = await create_pending_action(
            session,
            contact_id=ctx.deps.contact_id,
            kind=PendingActionKind.CREATE_EVENT,
            payload=payload,
            turn_id=str(execution_run_id),
            source_interaction_run_id=getattr(
                ctx.deps, "source_interaction_run_id", None
            ),
            source_execution_run_id=execution_run_id,
        )
        await session.commit()
    return json.dumps(
        {
            "status": "awaiting_later_confirmation",
            "action_id": str(row.id),
            "calendar_id": payload["calendar_id"],
            "title": payload["title"],
            "start": payload["start"],
            "end": payload["end"],
            "timezone": payload["timezone"],
            "all_day": payload["all_day"],
            "attendee_count": len(payload["attendees"]),
            "expires_in_minutes": 15,
        },
        ensure_ascii=False,
    )
