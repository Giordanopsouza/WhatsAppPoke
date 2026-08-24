"""Contact timezone + tool datetime parsing."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.core.timeutil import parse_tool_datetime, resolve_tz


def test_resolve_tz_fallback() -> None:
    assert resolve_tz(None).key == "America/Sao_Paulo"
    assert resolve_tz("Not/AZone").key == "America/Sao_Paulo"
    assert resolve_tz("America/New_York").key == "America/New_York"


def test_parse_tool_datetime_naive_uses_contact_tz() -> None:
    tz = ZoneInfo("America/Sao_Paulo")
    dt = parse_tool_datetime("2026-08-10T15:00", tz=tz)
    assert isinstance(dt, datetime)
    assert dt.tzinfo == tz
    assert parse_tool_datetime("2026-08-10", tz=tz) == date(2026, 8, 10)
