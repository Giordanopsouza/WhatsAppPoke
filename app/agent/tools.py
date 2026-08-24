"""Typed tools for the Pydantic AI agent."""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

import httpx
from pydantic_ai import RunContext

from app.core.config import settings
from app.database.models import Reminder, ReminderStatus, Task, TaskStatus
from app.db import (
    cancel_reminder_row,
    complete_task_row,
    create_reminder,
    create_task,
    enqueue_reminder_due,
    list_reminders_for_contact,
    list_tasks_for_contact,
)
from app.core.logutil import get_logger
from app.core.timeutil import parse_tool_datetime, resolve_tz

TAVILY_SEARCH_URL = "https://api.tavily.com/search"
TAVILY_TIMEOUT_SECONDS = 8.0

log = get_logger(__name__)


async def tavily_search(query: str) -> str:
    """Search the internet for up-to-date information.

    Use when the answer needs current facts, news, or web sources.
    """
    try:
        async with httpx.AsyncClient(timeout=TAVILY_TIMEOUT_SECONDS) as client:
            response = await client.post(
                TAVILY_SEARCH_URL,
                headers={"Authorization": f"Bearer {settings.tavily_api_key}"},
                json={"query": query, "max_results": 5},
            )
            response.raise_for_status()
            payload = response.json()
    except Exception as exc:
        return f"Error calling tavily_search: {exc}"

    results = [
        {
            "title": item.get("title", ""),
            "url": item.get("url", ""),
            "content": item.get("content", ""),
        }
        for item in payload.get("results", [])
    ]
    return json.dumps(results, ensure_ascii=False)


# Format a task due date for display in the user's timezone.
def _format_task_due(due_at: datetime | None, *, tz_name: str) -> str:
    if due_at is None:
        return ""
    tz = resolve_tz(tz_name)
    local = due_at.astimezone(tz) if due_at.tzinfo else due_at.replace(tzinfo=tz)
    return local.strftime("%d/%m %H:%M")


# One line of text describing a single task in a list.
def _format_task_line(task: Task, *, index: int, tz_name: str) -> str:
    due = _format_task_due(task.due_at, tz_name=tz_name)
    due_bit = f" — {due}" if due else ""
    mark = "✓" if task.status == TaskStatus.DONE else "·"
    return f"{index}. {mark} {task.title}{due_bit}"


async def add_task(
    ctx: RunContext[Any],
    title: str,
    due_at: str | None = None,
) -> str:
    """Add a todo to the person's list.

    title: short checklist text. due_at: optional ISO datetime in the person's
    local tz (YYYY-MM-DDTHH:MM). Tasks are silent until asked — for a timed
    ping use set_reminder instead (or both). For a recurring goal that uses
    tools, use create_automation instead.
    """
    title_clean = title.strip()
    if not title_clean:
        return "Preciso de um título pra tarefa."

    parsed_due: datetime | None = None
    if due_at is not None and due_at.strip():
        tz = resolve_tz(ctx.deps.tz)
        try:
            value = parse_tool_datetime(due_at, tz=tz)
        except ValueError:
            return (
                "Não entendi due_at. Use ISO: YYYY-MM-DDTHH:MM "
                "(horário local da pessoa)."
            )
        if isinstance(value, date) and not isinstance(value, datetime):
            return (
                "due_at precisa de horário (YYYY-MM-DDTHH:MM), não só a data."
            )
        assert isinstance(value, datetime)
        parsed_due = value

    async with ctx.deps.session_factory() as session:
        row = await create_task(
            session,
            contact_id=ctx.deps.contact_id,
            title=title_clean,
            due_at=parsed_due,
        )
        await session.commit()

    due = _format_task_due(row.due_at, tz_name=ctx.deps.tz)
    due_bit = f" (pra {due})" if due else ""
    return f"Tarefa adicionada: {row.title}{due_bit}."


async def list_tasks(ctx: RunContext[Any]) -> str:
    """List the person's todos. Open first; due dates in their tz.

    Call this before complete_task so you have the 1-based index. If the
    person is vague, ask which number — you choose; the tool only takes index.
    """
    async with ctx.deps.session_factory() as session:
        tasks = await list_tasks_for_contact(
            session,
            contact_id=ctx.deps.contact_id,
            include_done=True,
        )

    if not tasks:
        return "Nenhuma tarefa."

    open_tasks = [t for t in tasks if t.status == TaskStatus.OPEN]
    done_tasks = [t for t in tasks if t.status == TaskStatus.DONE]
    ordered = open_tasks + done_tasks

    lines = [
        _format_task_line(t, index=i, tz_name=ctx.deps.tz)
        for i, t in enumerate(ordered, start=1)
    ]
    return "Tarefas:\n" + "\n".join(lines)


async def complete_task(ctx: RunContext[Any], index: int) -> str:
    """Mark a todo done by 1-based index from list_tasks (open first).

    Call list_tasks first. If several could match what the person said, ask
    which number — do not invent an index.
    """
    if index < 1:
        return "Índice inválido. Chame list_tasks e use o número da lista."

    async with ctx.deps.session_factory() as session:
        tasks = await list_tasks_for_contact(
            session,
            contact_id=ctx.deps.contact_id,
            include_done=True,
        )
        ordered = [t for t in tasks if t.status == TaskStatus.OPEN] + [
            t for t in tasks if t.status == TaskStatus.DONE
        ]
        if not any(t.status == TaskStatus.OPEN for t in ordered):
            return "Não tem tarefa aberta pra concluir."
        if index > len(ordered):
            return (
                f"Não tem item {index}. Chame list_tasks e use um número "
                f"de 1 a {len(ordered)}."
            )

        task = ordered[index - 1]
        if task.status == TaskStatus.DONE:
            return f"Essa já está concluída: {task.title}."

        updated = await complete_task_row(session, task_id=task.id)
        await session.commit()

    if updated is None:
        return "Essa tarefa já foi concluída ou sumiu. Confira com list_tasks."
    return f"Concluída: {updated.title}."


# Format a reminder due time for display.
def _format_reminder_due(due_at: datetime, *, tz_name: str) -> str:
    tz = resolve_tz(tz_name)
    local = due_at.astimezone(tz) if due_at.tzinfo else due_at.replace(tzinfo=tz)
    return local.strftime("%d/%m %H:%M")


# One line of text describing a single reminder in a list.
def _format_reminder_line(
    reminder: Reminder,
    *,
    index: int,
    tz_name: str,
) -> str:
    due = _format_reminder_due(reminder.due_at, tz_name=tz_name)
    if reminder.status == ReminderStatus.ACTIVE:
        mark = "·"
    elif reminder.status == ReminderStatus.SENT:
        mark = "✓"
    else:
        mark = "×"
    return f"{index}. {mark} {reminder.body} — {due}"


async def set_reminder(
    ctx: RunContext[Any],
    body: str,
    due_at: str,
) -> str:
    """Schedule a WhatsApp ping at due_at (contact's local tz).

    body: text of the ping (sent as-is when due). due_at: ISO datetime YYYY-MM-DDTHH:MM —
    convert relative times ("amanhã 9h", "em 2 minutos") using the injected
    clock, then pass ISO. Rejects past times. To change a reminder: cancel
    then set again (no edit/reschedule tool). Recurring tool work is
    create_automation, not a reminder.
    """
    body_clean = body.strip()
    if not body_clean:
        return "Preciso do texto do lembrete."

    tz = resolve_tz(ctx.deps.tz)
    try:
        value = parse_tool_datetime(due_at, tz=tz)
    except ValueError:
        return (
            "Não entendi due_at. Use ISO: YYYY-MM-DDTHH:MM "
            "(horário local da pessoa)."
        )
    if isinstance(value, date) and not isinstance(value, datetime):
        return "due_at precisa de horário (YYYY-MM-DDTHH:MM), não só a data."
    assert isinstance(value, datetime)

    now = datetime.now(tz)
    due = value if value.tzinfo else value.replace(tzinfo=tz)
    if due <= now:
        return "due_at já passou — escolha um horário futuro."

    async with ctx.deps.session_factory() as session:
        row = await create_reminder(
            session,
            contact_id=ctx.deps.contact_id,
            body=body_clean,
            due_at=due,
        )
        await enqueue_reminder_due(
            session,
            contact_id=ctx.deps.contact_id,
            reminder_id=row.id,
            run_at=due,
        )
        await session.commit()

    when = _format_reminder_due(due, tz_name=ctx.deps.tz)
    return f"Lembrete marcado pra {when}: {row.body}."


async def list_reminders(ctx: RunContext[Any]) -> str:
    """List reminders. Active first; due times in the contact's tz.

    Call this before cancel_reminder so you have the 1-based index. If the
    person is vague, ask which number — you choose; the tool only takes index.
    """
    async with ctx.deps.session_factory() as session:
        rows = await list_reminders_for_contact(
            session,
            contact_id=ctx.deps.contact_id,
            include_inactive=True,
        )

    if not rows:
        return "Nenhum lembrete."

    active = [r for r in rows if r.status == ReminderStatus.ACTIVE]
    other = [r for r in rows if r.status != ReminderStatus.ACTIVE]
    ordered = active + other
    lines = [
        _format_reminder_line(r, index=i, tz_name=ctx.deps.tz)
        for i, r in enumerate(ordered, start=1)
    ]
    return "Lembretes:\n" + "\n".join(lines)


async def cancel_reminder(ctx: RunContext[Any], index: int) -> str:
    """Cancel an active reminder by 1-based index from list_reminders.

    Call list_reminders first. If several could match, ask which number.
    The wake-up job is left alone — check-at-fire no-ops cancelled rows.
    To reschedule: cancel then set_reminder.
    """
    if index < 1:
        return "Índice inválido. Chame list_reminders e use o número da lista."

    async with ctx.deps.session_factory() as session:
        rows = await list_reminders_for_contact(
            session,
            contact_id=ctx.deps.contact_id,
            include_inactive=True,
        )
        ordered = [r for r in rows if r.status == ReminderStatus.ACTIVE] + [
            r for r in rows if r.status != ReminderStatus.ACTIVE
        ]
        if not any(r.status == ReminderStatus.ACTIVE for r in ordered):
            return "Não tem lembrete ativo pra cancelar."
        if index > len(ordered):
            return (
                f"Não tem item {index}. Chame list_reminders e use um número "
                f"de 1 a {len(ordered)}."
            )

        reminder = ordered[index - 1]
        if reminder.status != ReminderStatus.ACTIVE:
            return f"Esse já não está ativo: {reminder.body}."

        updated = await cancel_reminder_row(session, reminder_id=reminder.id)
        await session.commit()

    if updated is None:
        return "Esse lembrete já foi cancelado ou disparou. Confira com list_reminders."
    return f"Lembrete cancelado: {updated.body}."
