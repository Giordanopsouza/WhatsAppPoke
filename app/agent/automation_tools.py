"""Owned Execution tools for contact-scoped RRULE automations.

A reminder is a stored-text ping. These tools schedule a goal that later
runs Execution; they never fire WhatsApp themselves.
"""

from __future__ import annotations

from datetime import datetime, timezone as utc_tz
from typing import Any

from pydantic_ai import RunContext

from app.agent.owned_tools import OWNED_TOOLKITS
from app.core.rrule import RRuleError, canonicalize_rrule, next_occurrence_utc, parse_timezone
from app.core.timeutil import resolve_tz
from app.database.models import Automation, AutomationStatus
from app.db import (
    create_automation_row,
    list_automations_for_contact,
    set_automation_status,
    upsert_automation_due,
)


# Parse comma-separated toolkit names from an automation tool argument.
def _parse_required_toolkits(raw: str | None) -> str | list[str]:
    if raw is None or not raw.strip():
        return []
    slugs: list[str] = []
    seen: set[str] = set()
    for part in raw.split(","):
        slug = part.strip().lower()
        if not slug:
            continue
        if slug == "local":
            continue
        if slug not in OWNED_TOOLKITS:
            return (
                "Toolkit não suportado nesta automação: "
                f"{slug}. Use gmail e/ou googlecalendar, ou deixe vazio."
            )
        if slug not in seen:
            seen.add(slug)
            slugs.append(slug)
    return slugs


# Format when an automation will run next.
def _format_next_run(next_run_at: datetime | None, *, tz_name: str) -> str:
    if next_run_at is None:
        return "sem próxima ocorrência"
    tz = resolve_tz(tz_name)
    local = (
        next_run_at.astimezone(tz)
        if next_run_at.tzinfo
        else next_run_at.replace(tzinfo=tz)
    )
    return local.strftime("%d/%m %H:%M")


# Symbol shown next to an automation in a list (active, paused, cancelled).
def _status_mark(status: AutomationStatus) -> str:
    if status == AutomationStatus.ACTIVE:
        return "·"
    if status == AutomationStatus.PAUSED:
        return "⏸"
    return "×"


# One line of text describing a single automation in a list.
def _format_automation_line(
    row: Automation, *, index: int, tz_name: str
) -> str:
    when = _format_next_run(row.next_run_at, tz_name=tz_name)
    mark = _status_mark(row.status)
    return f"{index}. {mark} {row.name} — {row.rrule} — {when}"


# Sort automations: active first, then paused, then the rest.
def _ordered_automations(rows: list[Automation]) -> list[Automation]:
    active = [r for r in rows if r.status == AutomationStatus.ACTIVE]
    paused = [r for r in rows if r.status == AutomationStatus.PAUSED]
    other = [
        r
        for r in rows
        if r.status not in (AutomationStatus.ACTIVE, AutomationStatus.PAUSED)
    ]
    return active + paused + other


async def create_automation(
    ctx: RunContext[Any],
    name: str,
    goal: str,
    rrule: str,
    required_toolkits: str = "",
    timezone: str | None = None,
) -> str:
    """Create a recurring automation (RRULE + goal), not a one-shot reminder.

    name: short label. goal: natural-language work to run at each occurrence.
    rrule: iCalendar RRULE body, e.g. FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR;BYHOUR=8;BYMINUTE=0.
    FREQ must be HOURLY/DAILY/WEEKLY/MONTHLY/YEARLY. Do not include DTSTART.
    required_toolkits: comma-separated owned slugs (gmail, googlecalendar) or empty
    for local-only work. timezone: IANA name; defaults to the contact timezone.

    Use set_reminder when the person wants a stored-text ping at one time.
    """
    name_clean = name.strip()
    goal_clean = " ".join(goal.split())
    if not name_clean:
        return "Preciso de um nome pra automação."
    if not goal_clean:
        return "Preciso da meta (goal) da automação."

    tz_name = (timezone or ctx.deps.tz).strip() or ctx.deps.tz
    try:
        parse_timezone(tz_name)
        canonical = canonicalize_rrule(rrule)
    except RRuleError as exc:
        return f"RRULE inválida: {exc}."

    toolkits = _parse_required_toolkits(required_toolkits)
    if isinstance(toolkits, str):
        return toolkits

    now = datetime.now(utc_tz.utc)
    try:
        next_run_at = next_occurrence_utc(
            rrule=canonical,
            timezone_name=tz_name,
            after=now,
            dtstart=now,
        )
    except RRuleError as exc:
        return f"RRULE inválida: {exc}."
    if next_run_at is None:
        return "Essa RRULE não tem ocorrência futura (COUNT/UNTIL esgotados ou horizonte)."

    async with ctx.deps.session_factory() as session:
        row = await create_automation_row(
            session,
            contact_id=ctx.deps.contact_id,
            name=name_clean,
            goal=goal_clean,
            rrule=canonical,
            timezone_name=tz_name,
            required_toolkits=toolkits,
            next_run_at=next_run_at,
        )
        await upsert_automation_due(
            session,
            contact_id=ctx.deps.contact_id,
            automation_id=row.id,
            run_at=next_run_at,
        )
        await session.commit()

    when = _format_next_run(next_run_at, tz_name=tz_name)
    return f"Automação criada: {row.name}. Próxima: {when}."


async def list_automations(ctx: RunContext[Any]) -> str:
    """List automations. Active first, then paused. Times in the contact tz.

    Call this before pause_automation, resume_automation, or cancel_automation.
    """
    async with ctx.deps.session_factory() as session:
        rows = await list_automations_for_contact(
            session, contact_id=ctx.deps.contact_id, include_cancelled=True
        )
    ordered = _ordered_automations(rows)
    if not ordered:
        return "Nenhuma automação."
    lines = [
        _format_automation_line(row, index=i, tz_name=ctx.deps.tz)
        for i, row in enumerate(ordered, start=1)
    ]
    return "Automações:\n" + "\n".join(lines)


async def pause_automation(ctx: RunContext[Any], index: int) -> str:
    """Pause an active automation by 1-based index from list_automations.

    The pending wake-up is left alone; a fire while paused is a no-op.
    """
    return await _set_listed_status(
        ctx, index, AutomationStatus.PAUSED, "Automação pausada"
    )


async def resume_automation(ctx: RunContext[Any], index: int) -> str:
    """Resume a paused automation by 1-based index from list_automations.

    Recomputes the next future occurrence; does not catch up missed pause time.
    """
    if index < 1:
        return "Índice inválido. Chame list_automations e use o número da lista."

    async with ctx.deps.session_factory() as session:
        rows = await list_automations_for_contact(
            session, contact_id=ctx.deps.contact_id, include_cancelled=True
        )
        ordered = _ordered_automations(rows)
        if index > len(ordered):
            return (
                f"Não tem item {index}. Chame list_automations e use um número "
                f"de 1 a {len(ordered)}."
                if ordered
                else "Nenhuma automação."
            )
        row = ordered[index - 1]
        if row.status != AutomationStatus.PAUSED:
            return f"Essa não está pausada: {row.name}."

        now = datetime.now(utc_tz.utc)
        try:
            next_run_at = next_occurrence_utc(
                rrule=row.rrule,
                timezone_name=row.timezone,
                after=now,
                dtstart=row.created_at,
            )
        except RRuleError as exc:
            return f"RRULE inválida: {exc}."
        if next_run_at is None:
            return "Essa RRULE não tem ocorrência futura pra retomar."

        updated = await set_automation_status(
            session,
            automation_id=row.id,
            contact_id=ctx.deps.contact_id,
            status=AutomationStatus.ACTIVE,
            next_run_at=next_run_at,
        )
        if updated is None:
            return "Não deu pra retomar. Confira com list_automations."
        await upsert_automation_due(
            session,
            contact_id=ctx.deps.contact_id,
            automation_id=updated.id,
            run_at=next_run_at,
        )
        await session.commit()

    when = _format_next_run(next_run_at, tz_name=ctx.deps.tz)
    return f"Automação retomada: {updated.name}. Próxima: {when}."


async def cancel_automation(ctx: RunContext[Any], index: int) -> str:
    """Cancel an active or paused automation by 1-based list index.

    The wake-up job is left alone — check-at-fire no-ops cancelled rows.
    """
    return await _set_listed_status(
        ctx, index, AutomationStatus.CANCELLED, "Automação cancelada"
    )


# Shared logic for pause/resume/cancel by list index.
async def _set_listed_status(
    ctx: RunContext[Any],
    index: int,
    status: AutomationStatus,
    ok_prefix: str,
) -> str:
    if index < 1:
        return "Índice inválido. Chame list_automations e use o número da lista."

    async with ctx.deps.session_factory() as session:
        rows = await list_automations_for_contact(
            session, contact_id=ctx.deps.contact_id, include_cancelled=True
        )
        ordered = _ordered_automations(rows)
        if not ordered:
            return "Nenhuma automação."
        if index > len(ordered):
            return (
                f"Não tem item {index}. Chame list_automations e use um número "
                f"de 1 a {len(ordered)}."
            )
        row = ordered[index - 1]
        if status == AutomationStatus.PAUSED and row.status != AutomationStatus.ACTIVE:
            return f"Essa não está ativa: {row.name}."
        if status == AutomationStatus.CANCELLED and row.status == AutomationStatus.CANCELLED:
            return f"Essa já foi cancelada: {row.name}."

        updated = await set_automation_status(
            session,
            automation_id=row.id,
            contact_id=ctx.deps.contact_id,
            status=status,
        )
        await session.commit()

    if updated is None:
        return "Não deu pra atualizar. Confira com list_automations."
    return f"{ok_prefix}: {updated.name}."
