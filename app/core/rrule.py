"""Contact-local iCalendar RRULE parsing and next-occurrence calculation.

Occurrences are wall-clock times in the automation timezone and persisted as
UTC. The next slot is whatever ``rrule.after`` returns; COUNT is capped so a
finite series cannot explode. Do not treat a found occurrence as exhausted
just because it is more than two years away — YEARLY intervals and leap-day
rules legitimately skip that far.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dateutil.rrule import rrulestr


ALLOWED_FREQ = frozenset({"YEARLY", "MONTHLY", "WEEKLY", "DAILY", "HOURLY"})
MAX_COUNT = 730
CATCH_UP_GRACE = timedelta(minutes=2)

_PART_ORDER = (
    "FREQ",
    "INTERVAL",
    "COUNT",
    "UNTIL",
    "BYMONTH",
    "BYMONTHDAY",
    "BYDAY",
    "BYHOUR",
    "BYMINUTE",
    "BYSECOND",
    "WKST",
    "BYSETPOS",
    "BYWEEKNO",
    "BYYEARDAY",
)


class RRuleError(ValueError):
    """Raised when an RRULE or timezone cannot be used for scheduling."""


# Turn a timezone name string into a ZoneInfo, or raise if it's invalid.
def parse_timezone(name: str) -> ZoneInfo:
    candidate = (name or "").strip()
    if not candidate:
        raise RRuleError("timezone is required")
    try:
        return ZoneInfo(candidate)
    except ZoneInfoNotFoundError as exc:
        raise RRuleError(f"unknown timezone: {candidate}") from exc


def canonicalize_rrule(raw: str) -> str:
    """Validate and return a canonical RRULE body (no RRULE: prefix)."""
    body = (raw or "").strip()
    if not body:
        raise RRuleError("rrule is empty")
    if body.upper().startswith("RRULE:"):
        body = body[6:].strip()
    if "\n" in body or "\r" in body:
        raise RRuleError("rrule must be a single RRULE line")
    upper = body.upper()
    if "DTSTART" in upper:
        raise RRuleError("DTSTART is stored separately; omit it from rrule")

    parsed: dict[str, str] = {}
    for part in body.split(";"):
        piece = part.strip()
        if not piece:
            continue
        if "=" not in piece:
            raise RRuleError(f"invalid rrule part: {piece}")
        key, value = piece.split("=", 1)
        key = key.strip().upper()
        value = value.strip()
        if not key or not value:
            raise RRuleError(f"invalid rrule part: {piece}")
        if key in parsed:
            raise RRuleError(f"duplicate rrule part: {key}")
        parsed[key] = value

    freq = parsed.get("FREQ", "").upper()
    if freq not in ALLOWED_FREQ:
        raise RRuleError(
            "FREQ must be HOURLY, DAILY, WEEKLY, MONTHLY, or YEARLY"
        )
    parsed["FREQ"] = freq

    interval_raw = parsed.get("INTERVAL", "1")
    try:
        interval = int(interval_raw)
    except ValueError as exc:
        raise RRuleError("INTERVAL must be a positive integer") from exc
    if interval < 1:
        raise RRuleError("INTERVAL must be a positive integer")
    parsed["INTERVAL"] = str(interval)

    if "COUNT" in parsed:
        try:
            count = int(parsed["COUNT"])
        except ValueError as exc:
            raise RRuleError("COUNT must be a positive integer") from exc
        if count < 1 or count > MAX_COUNT:
            raise RRuleError(f"COUNT must be between 1 and {MAX_COUNT}")
        parsed["COUNT"] = str(count)

    if "BYSECOND" not in parsed:
        parsed["BYSECOND"] = "0"

    canonical = ";".join(
        f"{key}={parsed[key]}" for key in _PART_ORDER if key in parsed
    )
    extra = [f"{key}={parsed[key]}" for key in parsed if key not in _PART_ORDER]
    if extra:
        canonical = ";".join([canonical, *extra]) if canonical else ";".join(extra)

    try:
        rrulestr(canonical, dtstart=datetime(2026, 1, 1, 0, 0, 0))
    except (ValueError, TypeError) as exc:
        raise RRuleError(f"invalid rrule: {exc}") from exc
    return canonical


# Convert a datetime to naive local wall-clock time in the given timezone.
def _naive_local(value: datetime, tz: ZoneInfo) -> datetime:
    if value.tzinfo is None:
        local = value.replace(tzinfo=tz)
    else:
        local = value.astimezone(tz)
    return local.replace(microsecond=0, tzinfo=None)


# Convert a naive local datetime to UTC.
def _to_utc(naive_local: datetime, tz: ZoneInfo) -> datetime:
    return naive_local.replace(tzinfo=tz).astimezone(timezone.utc)


def next_occurrence_utc(
    *,
    rrule: str,
    timezone_name: str,
    after: datetime,
    dtstart: datetime,
) -> datetime | None:
    """Next occurrence strictly after ``after``, as UTC, or None if exhausted."""
    tz = parse_timezone(timezone_name)
    body = canonicalize_rrule(rrule)
    start = _naive_local(dtstart, tz)
    after_local = _naive_local(after, tz)
    try:
        rule = rrulestr(body, dtstart=start)
    except (ValueError, TypeError) as exc:
        raise RRuleError(f"invalid rrule: {exc}") from exc
    nxt = rule.after(after_local, inc=False)
    if nxt is None:
        return None
    return _to_utc(nxt, tz)


def is_catch_up(*, scheduled_at: datetime, now: datetime) -> bool:
    """True when the due occurrence is late enough to count as a catch-up."""
    if scheduled_at.tzinfo is None:
        scheduled = scheduled_at.replace(tzinfo=timezone.utc)
    else:
        scheduled = scheduled_at.astimezone(timezone.utc)
    current = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    return current > scheduled + CATCH_UP_GRACE
