"""Fixed Google Calendar requests and compact provider-response normalization."""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime, timedelta
from typing import Any, Mapping
from urllib.parse import quote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.integrations.composio_proxy import ProxyParameter, ProxyRequest


MAX_CALENDARS = 25
MAX_EVENTS = 25
MAX_RANGE_DAYS = 31
MAX_TITLE_CHARS = 300
MAX_LOCATION_CHARS = 500
MAX_DESCRIPTION_CHARS = 4_000
MAX_ATTENDEES = 20
MAX_CALENDAR_ID_CHARS = 256
MAX_EVENT_ID_CHARS = 1024
MAX_EMAIL_CHARS = 320
_WHITESPACE_RE = re.compile(r"\s+")
_EMAIL_RE = re.compile(r"^[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+$")
_RELATIVE_KEYS = {
    "today": "today",
    "hoje": "today",
    "tomorrow": "tomorrow",
    "amanha": "tomorrow",
    "yesterday": "yesterday",
    "ontem": "yesterday",
    "this_week": "this_week",
    "esta_semana": "this_week",
    "next_week": "next_week",
    "proxima_semana": "next_week",
}


class CalendarPayloadError(ValueError):
    """A provider payload or owned-tool input violated the bounded contract."""


# Trim and shorten a string to a max length.
def compact(value: Any, *, limit: int) -> str:
    text = value if isinstance(value, str) else ""
    return _WHITESPACE_RE.sub(" ", text).strip()[:limit]


# Normalize text for matching relative date tokens (today, amanhã, etc.).
def _fold(value: str) -> str:
    return "".join(
        ch
        for ch in unicodedata.normalize("NFKD", value.casefold())
        if not unicodedata.combining(ch)
    ).strip()


# Validate a Google Calendar id string.
def validate_calendar_id(value: str) -> str:
    clean = value.strip()
    if not clean or len(clean) > MAX_CALENDAR_ID_CHARS or any(ch.isspace() for ch in clean):
        raise CalendarPayloadError("invalid calendar id")
    return clean


# Validate a Google Calendar event id string.
def validate_event_id(value: str) -> str:
    clean = value.strip()
    if not clean or len(clean) > MAX_EVENT_ID_CHARS or any(ch.isspace() for ch in clean):
        raise CalendarPayloadError("invalid event id")
    return clean


# Validate a timezone name (e.g. America/Sao_Paulo).
def validate_timezone(name: str) -> str:
    clean = name.strip()
    if not clean:
        raise CalendarPayloadError("invalid timezone")
    try:
        ZoneInfo(clean)
    except ZoneInfoNotFoundError:
        raise CalendarPayloadError("invalid timezone") from None
    return clean


# Validate an attendee email address for calendar events.
def validate_email_address(value: str) -> str:
    address = value.strip()
    if len(address) > MAX_EMAIL_CHARS or not _EMAIL_RE.fullmatch(address):
        raise CalendarPayloadError("invalid attendee email")
    return address


# Midnight on a given date in the contact's timezone.
def _start_of_day(day: date, tz: ZoneInfo) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=tz)


# Monday of the week containing the given date.
def _monday(day: date) -> date:
    return day - timedelta(days=day.weekday())


# Map a user token like "hoje" or "tomorrow" to our internal key.
def _relative_key(token: str) -> str | None:
    return _RELATIVE_KEYS.get(_fold(token).replace("-", "_").replace(" ", "_"))


# Turn "today", "this_week", etc. into a start/end datetime window.
def _relative_window(
    token: str, *, now: datetime, tz: ZoneInfo
) -> tuple[datetime, datetime] | None:
    key = _relative_key(token)
    if key is None:
        return None
    today = now.astimezone(tz).date()
    if key == "today":
        start = _start_of_day(today, tz)
        return start, start + timedelta(days=1)
    if key == "tomorrow":
        start = _start_of_day(today + timedelta(days=1), tz)
        return start, start + timedelta(days=1)
    if key == "yesterday":
        start = _start_of_day(today - timedelta(days=1), tz)
        return start, start + timedelta(days=1)
    if key == "this_week":
        start = _start_of_day(_monday(today), tz)
        return start, start + timedelta(days=7)
    start = _start_of_day(_monday(today) + timedelta(days=7), tz)
    return start, start + timedelta(days=7)


# Parse a date or datetime string into an aware datetime boundary.
def _parse_boundary(value: str, *, tz: ZoneInfo, end: bool) -> datetime:
    raw = value.strip()
    try:
        if len(raw) == 10 and raw[4] == "-" and raw[7] == "-":
            day = date.fromisoformat(raw)
            start = _start_of_day(day, tz)
            return start + timedelta(days=1) if end else start
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError):
        raise CalendarPayloadError("invalid date or datetime") from None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    return dt.astimezone(tz)


def resolve_query_window(
    *,
    time_min: str | None,
    time_max: str | None,
    tz: ZoneInfo,
    now: datetime,
) -> tuple[datetime, datetime]:
    """Resolve relative or ISO bounds against the contact clock.

    Provider timestamps are the returned aware datetimes, never the raw token.
    A date-only ``time_max`` is inclusive through the end of that local day.
    """
    if time_min is None and time_max is None:
        start = _start_of_day(now.astimezone(tz).date(), tz)
        return start, start + timedelta(days=1)
    if time_min is not None and time_max is None:
        relative = _relative_window(time_min, now=now, tz=tz)
        if relative is not None:
            return relative
        start = _parse_boundary(time_min, tz=tz, end=False)
        if len(time_min.strip()) == 10:
            return start, start + timedelta(days=1)
        raise CalendarPayloadError("time_max is required when time_min has a time")
    if time_min is None or time_max is None:
        raise CalendarPayloadError("time_min and time_max must be provided together")
    start_relative = _relative_window(time_min, now=now, tz=tz)
    finish_relative = _relative_window(time_max, now=now, tz=tz)
    start = start_relative[0] if start_relative else _parse_boundary(time_min, tz=tz, end=False)
    finish = finish_relative[1] if finish_relative else _parse_boundary(time_max, tz=tz, end=True)
    if start >= finish:
        raise CalendarPayloadError("time_min must be earlier than time_max")
    if finish - start > timedelta(days=MAX_RANGE_DAYS):
        raise CalendarPayloadError(f"date range cannot exceed {MAX_RANGE_DAYS} days")
    return start, finish


# Format a datetime for Google Calendar API query parameters.
def _rfc3339(value: datetime) -> str:
    return value.isoformat()


# Paths are relative to Composio's googlecalendar base
# (`https://www.googleapis.com/calendar/v3`). Do not prefix `/calendar/v3`.


# Build the proxy request to list calendars the user can read.
def list_calendars_request() -> ProxyRequest:
    return ProxyRequest(
        endpoint="/users/me/calendarList",
        method="GET",
        parameters=(
            ProxyParameter("maxResults", str(MAX_CALENDARS), "query"),
            ProxyParameter("minAccessRole", "reader", "query"),
        ),
    )


# Build the proxy request to list events in a time window.
def list_events_request(
    *,
    calendar_id: str,
    time_min: str | None,
    time_max: str | None,
    max_results: int,
    tz: ZoneInfo,
    now: datetime,
) -> ProxyRequest:
    clean_id = validate_calendar_id(calendar_id)
    if not 1 <= max_results <= MAX_EVENTS:
        raise CalendarPayloadError(f"max_results must be between 1 and {MAX_EVENTS}")
    start, finish = resolve_query_window(
        time_min=time_min, time_max=time_max, tz=tz, now=now
    )
    encoded = quote(clean_id, safe="")
    return ProxyRequest(
        endpoint=f"/calendars/{encoded}/events",
        method="GET",
        parameters=(
            ProxyParameter("timeMin", _rfc3339(start), "query"),
            ProxyParameter("timeMax", _rfc3339(finish), "query"),
            ProxyParameter("singleEvents", "true", "query"),
            ProxyParameter("orderBy", "startTime", "query"),
            ProxyParameter("maxResults", str(max_results), "query"),
            ProxyParameter("timeZone", str(tz), "query"),
        ),
    )


# Build the proxy request to fetch one calendar event.
def event_request(*, calendar_id: str, event_id: str, tz: ZoneInfo) -> ProxyRequest:
    clean_calendar = validate_calendar_id(calendar_id)
    clean_event = validate_event_id(event_id)
    return ProxyRequest(
        endpoint=(
            f"/calendars/{quote(clean_calendar, safe='')}"
            f"/events/{quote(clean_event, safe='')}"
        ),
        method="GET",
        parameters=(ProxyParameter("timeZone", str(tz), "query"),),
    )


def resolve_event_bounds(
    *,
    start: str,
    end: str,
    timezone: str,
    now: datetime,
) -> tuple[str, str, bool, str]:
    """Return explicit start/end strings, all-day flag, and validated timezone."""
    tz_name = validate_timezone(timezone)
    tz = ZoneInfo(tz_name)
    start_raw, end_raw = start.strip(), end.strip()
    start_relative = _relative_window(start_raw, now=now, tz=tz)
    end_relative = _relative_window(end_raw, now=now, tz=tz)
    start_is_date = start_relative is not None or (
        len(start_raw) == 10 and start_raw[4] == "-" and start_raw[7] == "-"
    )
    end_is_date = end_relative is not None or (
        len(end_raw) == 10 and end_raw[4] == "-" and end_raw[7] == "-"
    )
    if start_is_date != end_is_date:
        raise CalendarPayloadError("start and end must both be dates or both have times")
    if start_is_date:
        try:
            start_day = (
                start_relative[0].date()
                if start_relative is not None
                else date.fromisoformat(start_raw)
            )
            if end_relative is not None:
                end_day = end_relative[0].date()
            else:
                end_day = date.fromisoformat(end_raw)
        except ValueError:
            raise CalendarPayloadError("invalid date or datetime") from None
        if end_day <= start_day:
            end_day = start_day + timedelta(days=1)
        if end_day - start_day > timedelta(days=MAX_RANGE_DAYS):
            raise CalendarPayloadError(f"date range cannot exceed {MAX_RANGE_DAYS} days")
        return start_day.isoformat(), end_day.isoformat(), True, tz_name

    start_dt = _parse_boundary(start_raw, tz=tz, end=False)
    end_dt = _parse_boundary(end_raw, tz=tz, end=False)
    if start_dt >= end_dt:
        raise CalendarPayloadError("start must be earlier than end")
    if end_dt - start_dt > timedelta(days=MAX_RANGE_DAYS):
        raise CalendarPayloadError(f"date range cannot exceed {MAX_RANGE_DAYS} days")
    return (
        start_dt.replace(tzinfo=None).strftime("%Y-%m-%dT%H:%M:%S"),
        end_dt.replace(tzinfo=None).strftime("%Y-%m-%dT%H:%M:%S"),
        False,
        tz_name,
    )


def validate_create_payload(
    *,
    calendar_id: str,
    title: str,
    start: str,
    end: str,
    timezone: str,
    attendees: list[str] | None,
    location: str | None,
    description: str | None,
    now: datetime,
) -> dict[str, Any]:
    clean_title = compact(title, limit=MAX_TITLE_CHARS + 1)
    if not clean_title or len(clean_title) > MAX_TITLE_CHARS:
        raise CalendarPayloadError("invalid event title")
    clean_location = compact(location or "", limit=MAX_LOCATION_CHARS + 1)
    if len(clean_location) > MAX_LOCATION_CHARS:
        raise CalendarPayloadError("invalid event location")
    clean_description = (description or "").strip()
    if len(clean_description) > MAX_DESCRIPTION_CHARS:
        raise CalendarPayloadError("invalid event description")
    emails: list[str] = []
    for raw in attendees or []:
        emails.append(validate_email_address(raw))
    if len(emails) > MAX_ATTENDEES:
        raise CalendarPayloadError(f"attendees cannot exceed {MAX_ATTENDEES}")
    explicit_start, explicit_end, all_day, tz_name = resolve_event_bounds(
        start=start, end=end, timezone=timezone, now=now
    )
    return {
        "calendar_id": validate_calendar_id(calendar_id),
        "title": clean_title,
        "start": explicit_start,
        "end": explicit_end,
        "timezone": tz_name,
        "all_day": all_day,
        "attendees": emails,
        "location": clean_location,
        "description": clean_description,
    }


# Build the proxy request to create a calendar event from a validated payload.
def create_event_request(payload: Mapping[str, Any]) -> ProxyRequest:
    calendar_id = validate_calendar_id(str(payload.get("calendar_id") or ""))
    title = compact(payload.get("title"), limit=MAX_TITLE_CHARS)
    if not title:
        raise CalendarPayloadError("invalid event title")
    timezone = validate_timezone(str(payload.get("timezone") or ""))
    all_day = bool(payload.get("all_day"))
    start = str(payload.get("start") or "")
    end = str(payload.get("end") or "")
    if all_day:
        body_start: dict[str, str] = {"date": start}
        body_end: dict[str, str] = {"date": end}
    else:
        body_start = {"dateTime": start, "timeZone": timezone}
        body_end = {"dateTime": end, "timeZone": timezone}
    body: dict[str, Any] = {"summary": title, "start": body_start, "end": body_end}
    location = compact(payload.get("location"), limit=MAX_LOCATION_CHARS)
    if location:
        body["location"] = location
    description = payload.get("description")
    if isinstance(description, str) and description.strip():
        body["description"] = description.strip()[:MAX_DESCRIPTION_CHARS]
    attendees = payload.get("attendees")
    emails: list[str] = []
    if isinstance(attendees, list):
        emails = [validate_email_address(str(item)) for item in attendees]
        if emails:
            body["attendees"] = [{"email": email} for email in emails]
    encoded = quote(calendar_id, safe="")
    return ProxyRequest(
        endpoint=f"/calendars/{encoded}/events",
        method="POST",
        body=body,
        parameters=(
            ProxyParameter("sendUpdates", "all" if emails else "none", "query"),
        ),
    )


# Turn one calendar list item into a compact dict for the agent.
def normalize_calendar(data: Any) -> dict[str, Any] | None:
    if not isinstance(data, Mapping) or not isinstance(data.get("id"), str):
        return None
    if data.get("hidden") is True:
        return None
    return {
        "calendar_id": str(data["id"])[:MAX_CALENDAR_ID_CHARS],
        "title": compact(data.get("summary"), limit=MAX_TITLE_CHARS) or "(sem título)",
        "primary": data.get("primary") is True,
        "access_role": compact(data.get("accessRole"), limit=32),
        "timezone": compact(data.get("timeZone"), limit=64),
    }


# Turn a calendar list API response into a list of compact calendars.
def normalize_calendars(data: Any) -> list[dict[str, Any]]:
    if not isinstance(data, Mapping):
        return []
    items = data.get("items")
    if not isinstance(items, list):
        return []
    results: list[dict[str, Any]] = []
    for item in items:
        calendar = normalize_calendar(item)
        if calendar is not None:
            results.append(calendar)
        if len(results) >= MAX_CALENDARS:
            break
    return results


# Parse event start/end from Google Calendar API format into local display strings.
def _local_bound(value: Any, *, tz: ZoneInfo) -> tuple[str, bool] | None:
    if not isinstance(value, Mapping):
        return None
    raw_date = value.get("date")
    if isinstance(raw_date, str) and raw_date:
        return raw_date[:10], True
    raw_dt = value.get("dateTime")
    if not isinstance(raw_dt, str) or not raw_dt:
        return None
    try:
        dt = datetime.fromisoformat(raw_dt.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            event_tz = value.get("timeZone")
            dt = dt.replace(tzinfo=ZoneInfo(event_tz) if isinstance(event_tz, str) else tz)
        local = dt.astimezone(tz)
    except (TypeError, ValueError, ZoneInfoNotFoundError):
        return None
    return local.strftime("%Y-%m-%dT%H:%M"), False


# Extract attendee emails and RSVP status from an event payload.
def _attendees(data: Mapping[str, Any]) -> list[dict[str, str]]:
    raw = data.get("attendees")
    results: list[dict[str, str]] = []
    if not isinstance(raw, list):
        return results
    for item in raw[:MAX_ATTENDEES]:
        if not isinstance(item, Mapping) or not isinstance(item.get("email"), str):
            continue
        results.append(
            {
                "email": compact(item.get("email"), limit=MAX_EMAIL_CHARS),
                "status": compact(item.get("responseStatus"), limit=32),
            }
        )
    return results


# Turn one calendar event API response into a compact dict for the agent.
def normalize_event(
    data: Any, *, tz: ZoneInfo, calendar_id: str | None = None
) -> dict[str, Any] | None:
    if not isinstance(data, Mapping) or not isinstance(data.get("id"), str):
        return None
    if data.get("status") == "cancelled":
        return None
    start = _local_bound(data.get("start"), tz=tz)
    end = _local_bound(data.get("end"), tz=tz)
    if start is None or end is None:
        return None
    provider_calendar = data.get("organizer", {})
    resolved_calendar = calendar_id
    if not resolved_calendar and isinstance(provider_calendar, Mapping):
        email = provider_calendar.get("email")
        if isinstance(email, str):
            resolved_calendar = email
    return {
        "event_id": str(data["id"])[:MAX_EVENT_ID_CHARS],
        "calendar_id": (resolved_calendar or "")[:MAX_CALENDAR_ID_CHARS],
        "title": compact(data.get("summary"), limit=MAX_TITLE_CHARS) or "(sem título)",
        "start": start[0],
        "end": end[0],
        "all_day": start[1],
        "timezone": str(tz),
        "attendees": _attendees(data),
        "location": compact(data.get("location"), limit=MAX_LOCATION_CHARS),
        "status": compact(data.get("status"), limit=32) or "confirmed",
    }


# Turn an events list API response into compact event dicts.
def normalize_events(
    data: Any, *, tz: ZoneInfo, calendar_id: str
) -> list[dict[str, Any]]:
    if not isinstance(data, Mapping):
        return []
    items = data.get("items")
    if not isinstance(items, list):
        return []
    results: list[dict[str, Any]] = []
    for item in items:
        event = normalize_event(item, tz=tz, calendar_id=calendar_id)
        if event is not None:
            results.append(event)
        if len(results) >= MAX_EVENTS:
            break
    return results


# Extract the new event id from a create-event API response.
def normalize_created_event(data: Any) -> str:
    if not isinstance(data, Mapping) or not isinstance(data.get("id"), str):
        raise CalendarPayloadError("Calendar did not return an event id")
    return str(data["id"])[:MAX_EVENT_ID_CHARS]
