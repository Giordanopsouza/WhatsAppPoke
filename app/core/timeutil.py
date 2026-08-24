"""Contact-local timezone and datetime parsing for agent tools."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_TZ = "America/Sao_Paulo"


# Get a timezone object from a name, defaulting to São Paulo if invalid.
def resolve_tz(name: str | None) -> ZoneInfo:
    """Return a ZoneInfo; fall back to America/Sao_Paulo on bad/missing names."""
    candidate = (name or "").strip() or DEFAULT_TZ
    try:
        return ZoneInfo(candidate)
    except ZoneInfoNotFoundError:
        return ZoneInfo(DEFAULT_TZ)


# Parse a date or datetime string from an agent tool argument.
def parse_tool_datetime(value: str, *, tz: ZoneInfo) -> datetime | date:
    """Parse ISO date or datetime from a tool arg; naive datetimes use ``tz``."""
    raw = value.strip()
    if not raw:
        raise ValueError("empty datetime")
    if len(raw) == 10 and raw[4] == "-" and raw[7] == "-":
        return date.fromisoformat(raw)
    dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        return dt.replace(tzinfo=tz)
    return dt
