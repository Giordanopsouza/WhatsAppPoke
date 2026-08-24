"""Task tools: add / list / complete by list index."""

from __future__ import annotations

from datetime import datetime
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.tools import add_task, complete_task, list_tasks
from app.database.models import Task, TaskStatus


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
        turn_id="job-1",
    )
    return MagicMock(deps=deps)


def _task(
    *,
    title: str,
    status: TaskStatus = TaskStatus.OPEN,
    due_at: datetime | None = None,
) -> Task:
    row = MagicMock(spec=Task)
    row.id = uuid4()
    row.title = title
    row.status = status
    row.due_at = due_at
    return cast(Task, row)


async def test_add_task_persists_open_row() -> None:
    session = AsyncMock()
    created = _task(title="Alongar")
    ctx = _tool_ctx(session=session)

    with patch("app.agent.tools.create_task", new=AsyncMock(return_value=created)) as create:
        out = await add_task(ctx, title="  Alongar  ")

    create.assert_awaited_once()
    kwargs = create.await_args.kwargs
    assert kwargs["contact_id"] == 7
    assert kwargs["title"] == "Alongar"
    assert kwargs["due_at"] is None
    session.commit.assert_awaited_once()
    assert "Alongar" in out


async def test_add_task_parses_due_at_in_contact_tz() -> None:
    session = AsyncMock()
    tz = ZoneInfo("America/Sao_Paulo")
    due = datetime(2026, 8, 10, 9, 0, tzinfo=tz)
    created = _task(title="Ligar Ana", due_at=due)
    ctx = _tool_ctx(session=session)

    with patch("app.agent.tools.create_task", new=AsyncMock(return_value=created)) as create:
        out = await add_task(ctx, title="Ligar Ana", due_at="2026-08-10T09:00")

    assert create.await_args.kwargs["due_at"] == due
    assert "10/08 09:00" in out


async def test_add_task_rejects_date_only_due() -> None:
    ctx = _tool_ctx()
    out = await add_task(ctx, title="x", due_at="2026-08-10")
    assert "horário" in out.casefold()


async def test_list_tasks_open_first_with_local_due() -> None:
    tz = ZoneInfo("America/Sao_Paulo")
    open_late = _task(
        title="Open late",
        due_at=datetime(2026, 8, 12, 18, 0, tzinfo=tz),
    )
    done = _task(
        title="Done one",
        status=TaskStatus.DONE,
        due_at=datetime(2026, 8, 11, 10, 0, tzinfo=tz),
    )
    open_early = _task(
        title="Open early",
        due_at=datetime(2026, 8, 11, 9, 0, tzinfo=tz),
    )
    rows = [open_early, open_late, done]
    ctx = _tool_ctx()

    with patch(
        "app.agent.tools.list_tasks_for_contact",
        new=AsyncMock(return_value=rows),
    ):
        out = await list_tasks(ctx)

    lines = out.splitlines()
    assert lines[0] == "Tarefas:"
    assert "1. · Open early — 11/08 09:00" in lines[1]
    assert "2. · Open late — 12/08 18:00" in lines[2]
    assert "3. ✓ Done one — 11/08 10:00" in lines[3]


async def test_complete_task_by_list_index() -> None:
    a, b = _task(title="A"), _task(title="B")
    updated = _task(title="B", status=TaskStatus.DONE)
    updated.id = b.id
    session = AsyncMock()
    ctx = _tool_ctx(session=session)

    with (
        patch(
            "app.agent.tools.list_tasks_for_contact",
            new=AsyncMock(return_value=[a, b]),
        ),
        patch(
            "app.agent.tools.complete_task_row",
            new=AsyncMock(return_value=updated),
        ) as complete,
    ):
        out = await complete_task(ctx, 2)

    complete.assert_awaited_once_with(session, task_id=b.id)
    assert out == "Concluída: B."


async def test_complete_task_rejects_out_of_range() -> None:
    a = _task(title="A")
    ctx = _tool_ctx()

    with (
        patch(
            "app.agent.tools.list_tasks_for_contact",
            new=AsyncMock(return_value=[a]),
        ),
        patch("app.agent.tools.complete_task_row", new=AsyncMock()) as complete,
    ):
        out = await complete_task(ctx, 9)

    complete.assert_not_awaited()
    assert "Não tem item 9" in out


async def test_complete_task_already_done() -> None:
    done = _task(title="Feita", status=TaskStatus.DONE)
    open_task = _task(title="Aberta")
    ctx = _tool_ctx()

    with (
        patch(
            "app.agent.tools.list_tasks_for_contact",
            new=AsyncMock(return_value=[open_task, done]),
        ),
        patch("app.agent.tools.complete_task_row", new=AsyncMock()) as complete,
    ):
        out = await complete_task(ctx, 2)

    complete.assert_not_awaited()
    assert "já está concluída" in out
