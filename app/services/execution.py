"""Deterministic lifecycle for detached, best-effort Execution runs."""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta
from typing import Literal

import logfire
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.execution import (
    ExecutionDeps,
    ExecutionOutcome,
    LOCAL_TOOLKIT,
    run_execution_goal,
)
from app.agent.gmail_tools import goal_implies_email_send
from app.core.config import settings
from app.core.logutil import get_logger
from app.database.models import (
    Contact,
    ExecutionRun,
    ExecutionRunStatus,
    PendingAction,
    PendingActionKind,
)
from app.db import (
    SessionLocal,
    abandon_stale_execution_runs,
    append_execution_event,
    claim_execution_run,
    create_or_get_execution_run,
    finish_execution_run,
    get_execution_run_by_dedupe,
    list_active_integrations,
    mark_execution_event_processed,
    reclaim_execution_run_for_retry,
    request_execution_cancellation,
)


log = get_logger(__name__)

DispatchState = Literal["started", "deduped", "busy", "unavailable", "cancelled"]


@dataclass(frozen=True)
class DispatchOutcome:
    state: DispatchState
    execution_run_id: uuid.UUID | None = None
    detail: str | None = None

    # JSON string the Interaction agent reads after starting an execution.
    def as_tool_result(self) -> str:
        """A structured, model-readable result for Interaction."""
        return json.dumps(
            {
                "state": self.state,
                "execution_id": str(self.execution_run_id)
                if self.execution_run_id
                else None,
                "detail": self.detail,
            },
            ensure_ascii=False,
        )


class LocalExecutionTasks:
    """Process-local handles for cancellation, keyed by one run id.

    This is deliberately not a roster or cross-contact batch manager.  The
    database is the lifecycle/audit source of truth; these handles disappear
    on API restart and stale database rows are then abandoned.
    """

    def __init__(self) -> None:
        self._tasks: dict[uuid.UUID, asyncio.Task[None]] = {}

    # Track in-memory asyncio tasks so we can cancel a running execution.
    def register(self, execution_run_id: uuid.UUID, task: asyncio.Task[None]) -> None:
        self._tasks[execution_run_id] = task
        task.add_done_callback(lambda _: self._tasks.pop(execution_run_id, None))

    # Cancel a running execution task if it's still in this process.
    def cancel(self, execution_run_id: uuid.UUID) -> bool:
        task = self._tasks.get(execution_run_id)
        if task is None or task.done():
            return False
        task.cancel()
        return True


local_execution_tasks = LocalExecutionTasks()


# Normalize the requested scope; omit blanks and make ordering irrelevant.
def _normalize_toolkits(toolkits: Sequence[str] | None) -> tuple[str, ...]:
    normalized = {
        toolkit.strip().lower()
        for toolkit in toolkits or ()
        if toolkit.strip()
    }
    return tuple(sorted(normalized)) or (LOCAL_TOOLKIT,)


# Hash goal + complete toolkit scope into a stable dedupe key for execution runs.
def _dedupe_key(*, goal: str, toolkits: Sequence[str]) -> str:
    normalized_goal = " ".join(goal.split()).casefold()
    normalized_scope = "\0".join(toolkits)
    value = f"{normalized_scope}\0{normalized_goal}".encode()
    return hashlib.sha256(value).hexdigest()


# Trim and shorten text for logs and stored summaries.
def _compact(value: str, *, limit: int = 800) -> str:
    clean = " ".join(value.split())
    return clean[:limit]


# List which non-local toolkits the contact still needs to connect.
async def _toolkits_are_available(
    *,
    contact_id: int,
    toolkits: Sequence[str],
    session_factory: async_sessionmaker[AsyncSession],
) -> list[str]:
    """Return the missing non-local toolkits (empty if all connected)."""
    required = [
        toolkit.strip().lower()
        for toolkit in toolkits
        if toolkit.strip().lower() not in ("", LOCAL_TOOLKIT)
    ]
    if not required:
        return []
    async with session_factory() as session:
        rows = await list_active_integrations(session, contact_id=contact_id)
    connected = {row.provider for row in rows}
    return [toolkit for toolkit in required if toolkit not in connected]


# Start a detached execution from Interaction (runs in background on the API).
async def dispatch_execution(
    *,
    contact_id: int,
    tz: str,
    goal: str,
    toolkits: Sequence[str] | None = None,
    session_factory: async_sessionmaker[AsyncSession] = SessionLocal,
    source_interaction_run_id: uuid.UUID | None = None,
) -> DispatchOutcome:
    """Create/dedupe one run and immediately start its best-effort task."""
    goal_clean = " ".join(goal.split())
    if not goal_clean:
        return DispatchOutcome("unavailable", detail="goal is empty")

    scope = _normalize_toolkits(toolkits)
    missing = await _toolkits_are_available(
        contact_id=contact_id,
        toolkits=scope,
        session_factory=session_factory,
    )
    if missing:
        return DispatchOutcome(
            "unavailable",
            detail="required toolkit is not connected: " + ", ".join(missing),
        )

    async with session_factory() as session:
        run, created = await create_or_get_execution_run(
            session,
            contact_id=contact_id,
            goal=goal_clean,
            toolkit_scope=list(scope),
            dedupe_key=_dedupe_key(goal=goal_clean, toolkits=scope),
        )
        await session.commit()

    if run is None:
        return DispatchOutcome("busy", detail="two active executions already exist")
    if not created:
        return DispatchOutcome("deduped", execution_run_id=run.id)

    task = asyncio.create_task(
        _run_execution(
            execution_run_id=run.id,
            contact_id=contact_id,
            tz=tz,
            goal=goal_clean,
            toolkit=LOCAL_TOOLKIT,
            session_factory=session_factory,
            source_interaction_run_id=source_interaction_run_id,
            toolkit_scope=scope,
        ),
        name=f"execution-{run.id}",
    )
    local_execution_tasks.register(run.id, task)
    return DispatchOutcome("started", execution_run_id=run.id)


async def run_scheduled_execution(
    *,
    contact_id: int,
    tz: str,
    goal: str,
    toolkit_scope: Sequence[str],
    dedupe_key: str,
    session_factory: async_sessionmaker[AsyncSession] = SessionLocal,
) -> DispatchOutcome:
    """Await one durable Execution + Interaction re-entry for a scheduled wake-up.

    Reuses the same create/claim/finish lifecycle as conversation dispatch.
    A retried wake-up resumes the same ``execution_run`` instead of inserting
    another row for the same occurrence.
    """
    goal_clean = " ".join(goal.split())
    if not goal_clean:
        return DispatchOutcome("unavailable", detail="goal is empty")

    scope = tuple(
        dict.fromkeys(
            toolkit.strip().lower() or LOCAL_TOOLKIT for toolkit in toolkit_scope
        )
    ) or (LOCAL_TOOLKIT,)
    missing = await _toolkits_are_available(
        contact_id=contact_id, toolkits=scope, session_factory=session_factory
    )
    if missing:
        return DispatchOutcome(
            "unavailable",
            detail="required toolkit is not connected: " + ", ".join(missing),
        )

    async with session_factory() as session:
        existing = await get_execution_run_by_dedupe(
            session, contact_id=contact_id, dedupe_key=dedupe_key
        )
        run: ExecutionRun | None = existing
        if existing is not None and existing.status in (
            ExecutionRunStatus.RUNNING,
            ExecutionRunStatus.CANCEL_REQUESTED,
        ):
            reclaimed = await reclaim_execution_run_for_retry(
                session,
                execution_run_id=existing.id,
                contact_id=contact_id,
            )
            if reclaimed is None:
                # Another worker already reclaimed or finished this row.
                return DispatchOutcome(
                    "busy",
                    execution_run_id=existing.id,
                    detail="reclaimed_by_another_worker",
                )
            run = reclaimed
        elif existing is None:
            run, _created = await create_or_get_execution_run(
                session,
                contact_id=contact_id,
                goal=goal_clean,
                toolkit_scope=list(scope),
                dedupe_key=dedupe_key,
            )
        await session.commit()

    if run is None:
        return DispatchOutcome("busy", detail="two active executions already exist")
    if run.status in (
        ExecutionRunStatus.SUCCEEDED,
        ExecutionRunStatus.FAILED,
        ExecutionRunStatus.TIMED_OUT,
        ExecutionRunStatus.CANCELLED,
        ExecutionRunStatus.ABANDONED,
    ):
        return DispatchOutcome("deduped", execution_run_id=run.id, detail="already_terminal")

    await _run_execution(
        execution_run_id=run.id,
        contact_id=contact_id,
        tz=tz,
        goal=goal_clean,
        toolkit=LOCAL_TOOLKIT,
        session_factory=session_factory,
        toolkit_scope=scope,
        await_reentry=True,
    )
    async with session_factory() as session:
        finished = await session.get(ExecutionRun, run.id)
        await session.commit()
    if finished is None or finished.status in (
        ExecutionRunStatus.PENDING,
        ExecutionRunStatus.RUNNING,
        ExecutionRunStatus.CANCEL_REQUESTED,
    ):
        raise RuntimeError("scheduled execution did not reach a terminal state")
    return DispatchOutcome("started", execution_run_id=run.id, detail=finished.status.value)


# User asked to cancel a running execution — mark it and stop the asyncio task.
async def cancel_execution(
    *,
    contact_id: int,
    execution_run_id: uuid.UUID,
    session_factory: async_sessionmaker[AsyncSession] = SessionLocal,
) -> DispatchOutcome:
    """Request cancellation and converge on one persisted terminal event."""
    async with session_factory() as session:
        row = await request_execution_cancellation(
            session, execution_run_id=execution_run_id, contact_id=contact_id
        )
        await session.commit()
    if row is None:
        return DispatchOutcome("unavailable", detail="execution is not active for contact")

    local_execution_tasks.cancel(execution_run_id)
    # A task cancelled before its first await never enters its coroutine.  A
    # separate finalizer closes that race; finish_execution_run is exactly-once.
    asyncio.create_task(
        _finish_and_schedule_reentry(
            execution_run_id=execution_run_id,
            contact_id=contact_id,
            status=ExecutionRunStatus.CANCELLED,
            goal=row.goal,
            session_factory=session_factory,
        ),
        name=f"execution-cancel-{execution_run_id}",
    )
    return DispatchOutcome("cancelled", execution_run_id=execution_run_id)


async def _email_send_was_staged(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    contact_id: int,
    execution_run_id: uuid.UUID,
) -> bool:
    async with session_factory() as session:
        staged = await session.scalar(
            select(PendingAction.id).where(
                PendingAction.contact_id == contact_id,
                PendingAction.source_execution_run_id == execution_run_id,
                PendingAction.kind == PendingActionKind.SEND_EMAIL,
            ).limit(1)
        )
        await session.commit()
    return staged is not None


async def _validate_email_send_staging(
    *,
    goal: str,
    toolkit_scope: Sequence[str],
    output: ExecutionOutcome,
    contact_id: int,
    execution_run_id: uuid.UUID,
    session_factory: async_sessionmaker[AsyncSession],
) -> ExecutionOutcome:
    if output.status != "succeeded":
        return output
    if "gmail" not in toolkit_scope or not goal_implies_email_send(goal):
        return output
    if await _email_send_was_staged(
        session_factory,
        contact_id=contact_id,
        execution_run_id=execution_run_id,
    ):
        return output
    return ExecutionOutcome(
        status="failed",
        summary="Não encenei o envio do e-mail para confirmação.",
    )


# Run one execution: call the agent, handle timeout/errors, then notify Interaction.
async def _run_execution(
    *,
    execution_run_id: uuid.UUID,
    contact_id: int,
    tz: str,
    goal: str,
    toolkit: str,
    session_factory: async_sessionmaker[AsyncSession],
    source_interaction_run_id: uuid.UUID | None = None,
    toolkit_scope: Sequence[str] | None = None,
    await_reentry: bool = False,
) -> None:
    scope = tuple(toolkit_scope) if toolkit_scope is not None else (toolkit,)
    trace_attributes = {}
    if source_interaction_run_id is not None:
        trace_attributes["source_interaction_run_id"] = str(source_interaction_run_id)

    with logfire.span(
        "execution",
        contact_id=contact_id,
        execution_run_id=str(execution_run_id),
        toolkit_scope=",".join(scope),
        **trace_attributes,
    ) as execution_span:
        async with session_factory() as session:
            claimed = await claim_execution_run(
                session, execution_run_id=execution_run_id, contact_id=contact_id
            )
            await session.commit()
        if claimed is None:
            execution_span.set_attribute("execution.status", "not_claimed")
            return

        try:
            output = await asyncio.wait_for(
                run_execution_goal(
                    goal=goal,
                    toolkit=toolkit if toolkit_scope is None else None,
                    toolkits=scope if toolkit_scope is not None else None,
                    deps=ExecutionDeps(
                        contact_id=contact_id,
                        session_factory=session_factory,
                        tz=tz,
                        execution_run_id=execution_run_id,
                        goal=goal,
                        source_interaction_run_id=source_interaction_run_id,
                        toolkit_scope=scope,
                    ),
                ),
                timeout=settings.execution_timeout_seconds,
            )
            output = await _validate_email_send_staging(
                goal=goal,
                toolkit_scope=scope,
                output=output,
                contact_id=contact_id,
                execution_run_id=execution_run_id,
                session_factory=session_factory,
            )
        except asyncio.TimeoutError:
            execution_span.set_attribute("execution.status", "timed_out")
            log.info(
                "execution_timed_out",
                extra={
                    "event": "execution_timed_out",
                    "contact_id": contact_id,
                    "execution_run_id": str(execution_run_id),
                },
            )
            await _finish_and_schedule_reentry(
                execution_run_id=execution_run_id,
                contact_id=contact_id,
                status=ExecutionRunStatus.TIMED_OUT,
                goal=goal,
                error="execution timeout",
                session_factory=session_factory,
                source_interaction_run_id=source_interaction_run_id,
                await_reentry=await_reentry,
            )
        except asyncio.CancelledError:
            execution_span.set_attribute("execution.status", "cancelled")
            await _finish_and_schedule_reentry(
                execution_run_id=execution_run_id,
                contact_id=contact_id,
                status=ExecutionRunStatus.CANCELLED,
                goal=goal,
                session_factory=session_factory,
                source_interaction_run_id=source_interaction_run_id,
                await_reentry=await_reentry,
            )
            raise
        except Exception as exc:
            execution_span.set_attribute("execution.status", "failed")
            execution_span.set_level("error")
            log.exception(
                "execution_failed",
                extra={
                    "event": "execution_failed",
                    "contact_id": contact_id,
                    "execution_run_id": str(execution_run_id),
                },
            )
            await _finish_and_schedule_reentry(
                execution_run_id=execution_run_id,
                contact_id=contact_id,
                status=ExecutionRunStatus.FAILED,
                goal=goal,
                error=_compact(str(exc), limit=240),
                session_factory=session_factory,
                source_interaction_run_id=source_interaction_run_id,
                await_reentry=await_reentry,
            )
        else:
            status = {
                "succeeded": ExecutionRunStatus.SUCCEEDED,
                "failed": ExecutionRunStatus.FAILED,
                "needs_input": ExecutionRunStatus.FAILED,
            }[output.status]
            execution_span.set_attribute("execution.status", status.value)
            execution_span.set_attribute("execution.outcome", output.status)
            await _finish_and_schedule_reentry(
                execution_run_id=execution_run_id,
                contact_id=contact_id,
                status=status,
                goal=goal,
                result={
                    "summary": _compact(output.summary),
                    "outcome": output.status,
                },
                session_factory=session_factory,
                source_interaction_run_id=source_interaction_run_id,
                await_reentry=await_reentry,
            )


async def _finish_and_schedule_reentry(
    *,
    execution_run_id: uuid.UUID,
    contact_id: int,
    status: ExecutionRunStatus,
    goal: str,
    result: dict[str, str] | None = None,
    error: str | None = None,
    session_factory: async_sessionmaker[AsyncSession],
    source_interaction_run_id: uuid.UUID | None = None,
    await_reentry: bool = False,
) -> None:
    """Persist one terminal event, then let Interaction consume it separately."""
    async with session_factory() as session:
        finished = await finish_execution_run(
            session,
            execution_run_id=execution_run_id,
            contact_id=contact_id,
            status=status,
            result=result,
            error=error,
        )
        if finished is None:
            await session.commit()
            return
        event = await append_execution_event(
            session,
            contact_id=contact_id,
            execution_run_id=execution_run_id,
            kind=f"execution.{status.value}",
            payload={
                "execution_id": str(execution_run_id),
                "goal": _compact(goal, limit=240),
                "status": status.value,
                "result": result or {},
                "error": error,
            },
        )
        await session.commit()

    if await_reentry:
        try:
            await _reenter_interaction(
                event_id=event.id,
                execution_run_id=execution_run_id,
                contact_id=contact_id,
                goal=goal,
                status=status,
                result=result,
                error=error,
                session_factory=session_factory,
                source_interaction_run_id=source_interaction_run_id,
            )
        except Exception:
            log.exception(
                "execution_reentry_failed_sync",
                extra={
                    "event": "execution_reentry_failed_sync",
                    "execution_run_id": str(execution_run_id),
                    "contact_id": contact_id,
                },
            )
        return

    asyncio.create_task(
        _reenter_interaction(
            event_id=event.id,
            execution_run_id=execution_run_id,
            contact_id=contact_id,
            goal=goal,
            status=status,
            result=result,
            error=error,
            session_factory=session_factory,
            source_interaction_run_id=source_interaction_run_id,
        ),
        name=f"execution-result-{execution_run_id}",
    )


# After execution ends: save result to DB and wake up Interaction to reply.
async def _reenter_interaction(
    *,
    event_id: uuid.UUID,
    execution_run_id: uuid.UUID,
    contact_id: int,
    goal: str,
    status: ExecutionRunStatus,
    result: dict[str, str] | None,
    error: str | None,
    session_factory: async_sessionmaker[AsyncSession],
    source_interaction_run_id: uuid.UUID | None = None,
) -> None:
    """Re-enter via Interaction, which reloads current visible history under its lock."""
    async with session_factory() as session:
        contact = await session.get(Contact, contact_id)
        await session.commit()
    if contact is None:
        return

    summary = json.dumps(
        {
            "execution_id": str(execution_run_id),
            "goal": _compact(goal, limit=240),
            "status": status.value,
            "result": result or {},
            "error": error,
        },
        ensure_ascii=False,
    )
    try:
        # Local import preserves the one-way execution/transport boundary.
        from app.agent.interaction import run_interaction_event

        reentry_attributes = {}
        if source_interaction_run_id is not None:
            reentry_attributes["source_interaction_run_id"] = str(
                source_interaction_run_id
            )
        with logfire.span(
            "execution.result_reentry",
            contact_id=contact_id,
            execution_run_id=str(execution_run_id),
            status=status.value,
            **reentry_attributes,
        ):
            delivery = await run_interaction_event(
                contact_id=contact_id,
                phone=contact.phone,
                provider_message_id=f"execution:{execution_run_id}",
                internal_event_summary=summary,
                event_kind="execution_result",
                require_visible_delivery=True,
            )
    except Exception:
        log.exception(
            "execution_reentry_failed",
            extra={"event": "execution_reentry_failed", "contact_id": contact_id, "execution_run_id": str(execution_run_id)},
        )
        return

    if delivery is None:
        log.warning(
            "execution_reentry_not_delivered",
            extra={
                "event": "execution_reentry_not_delivered",
                "contact_id": contact_id,
                "execution_run_id": str(execution_run_id),
                "delivery_path": "none_or_failed",
                "processed_decision": False,
            },
        )
        return

    async with session_factory() as session:
        await mark_execution_event_processed(
            session, event_id=event_id, contact_id=contact_id
        )
        await session.commit()


async def abandon_expired_executions(
    *, session_factory: async_sessionmaker[AsyncSession] = SessionLocal
) -> int:
    """Startup cleanup only: do not replay or notify orphaned work."""
    async with session_factory() as session:
        count = await abandon_stale_execution_runs(
            session,
            older_than=timedelta(seconds=settings.execution_timeout_seconds),
        )
        await session.commit()
    if count:
        with logfire.span("execution.abandonment", count=count):
            log.info(
                "execution_abandoned",
                extra={"event": "execution_abandoned", "count": count},
            )
    return count
