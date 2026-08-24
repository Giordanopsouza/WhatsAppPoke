"""Claim and execute a Calendar create selected by Interaction confirmation."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Literal
import uuid

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database.models import PendingAction, PendingActionKind
from app.db import (
    claim_pending_action,
    discard_pending_action,
    fail_pending_action,
    list_active_integrations,
    list_pending_actions,
    payload_hash,
    release_pending_action,
)
from app.integrations.calendar import CalendarPayloadError, create_event_request
from app.integrations.composio_proxy import AuthenticatedProxyAdapter, AuthenticatedProxyError


ConfirmationState = Literal[
    "none", "ambiguous", "created", "retryable_failure", "invalid"
]


@dataclass(frozen=True)
class EventConfirmationOutcome:
    state: ConfirmationState
    detail: str
    action_id: str | None = None


# Pick which staged calendar event the user is confirming.
def _select_event_action(
    rows: list[PendingAction], action_id: uuid.UUID | None
) -> PendingAction | None:
    if action_id is not None:
        return next(
            (
                row
                for row in rows
                if row.id == action_id and row.kind == PendingActionKind.CREATE_EVENT
            ),
            None,
        )
    event_rows = [row for row in rows if row.kind == PendingActionKind.CREATE_EVENT]
    if len(event_rows) == 1:
        return event_rows[0]
    return None


# User confirmed a staged event — claim it and create on Google Calendar.
async def confirm_staged_event(
    *,
    contact_id: int,
    inbound_turn_id: str,
    session_factory: async_sessionmaker[AsyncSession],
    action_id: uuid.UUID | None = None,
    proxy: AuthenticatedProxyAdapter | None = None,
) -> EventConfirmationOutcome:
    """Claim and create one fixed event selected by the Interaction Agent."""
    async with session_factory() as session:
        candidates = await list_pending_actions(session, contact_id=contact_id)
        selected = _select_event_action(candidates, action_id)
        if selected is None:
            event_count = sum(
                1 for row in candidates if row.kind == PendingActionKind.CREATE_EVENT
            )
            if action_id is None and event_count > 1:
                return EventConfirmationOutcome(
                    "ambiguous",
                    "há mais de uma ação pendente; pergunte qual deve ser confirmada",
                )
            return EventConfirmationOutcome("none", "não há ação pendente válida")
        expected_hash = payload_hash(selected.payload)
        if expected_hash != selected.payload_hash:
            claimed = await claim_pending_action(
                session,
                contact_id=contact_id,
                kind=PendingActionKind.CREATE_EVENT,
                turn_id=inbound_turn_id,
                action_id=selected.id,
                payload_hash=selected.payload_hash,
            )
            if claimed is not None:
                await fail_pending_action(session, action_id=claimed.id)
                await session.commit()
            return EventConfirmationOutcome("invalid", "ação pendente inválida")
        claimed = await claim_pending_action(
            session,
            contact_id=contact_id,
            kind=PendingActionKind.CREATE_EVENT,
            turn_id=inbound_turn_id,
            action_id=selected.id,
            payload_hash=expected_hash,
        )
        await session.commit()

    if claimed is None:
        return EventConfirmationOutcome("none", "ação expirada, já usada ou criada neste turno")
    try:
        request = create_event_request(claimed.payload)
    except CalendarPayloadError:
        async with session_factory() as session:
            await fail_pending_action(session, action_id=claimed.id)
            await session.commit()
        return EventConfirmationOutcome("invalid", "ação pendente inválida", str(claimed.id))

    async with session_factory() as session:
        integrations = await list_active_integrations(session, contact_id=contact_id)
    integration = next((row for row in integrations if row.provider == "googlecalendar"), None)
    if integration is None:
        async with session_factory() as session:
            await release_pending_action(session, action_id=claimed.id)
            await session.commit()
        return EventConfirmationOutcome(
            "retryable_failure", "Agenda não está conectada", str(claimed.id)
        )

    adapter = proxy or AuthenticatedProxyAdapter(toolkit="googlecalendar")
    try:
        response = await asyncio.to_thread(
            adapter.execute,
            contact_id=contact_id,
            integration=integration,
            owned_tool_name="execute_confirmed_event_create",
            request=request,
        )
    except AuthenticatedProxyError:
        async with session_factory() as session:
            await release_pending_action(session, action_id=claimed.id)
            await session.commit()
        return EventConfirmationOutcome(
            "retryable_failure",
            "não consegui criar agora; a confirmação pode ser tentada de novo",
            str(claimed.id),
        )

    async with session_factory() as session:
        await discard_pending_action(session, action_id=claimed.id)
        await session.commit()
    provider_id = response.data.get("id") if isinstance(response.data, dict) else None
    detail = "evento criado"
    if isinstance(provider_id, str):
        detail += f"; event_id={provider_id[:256]}"
    return EventConfirmationOutcome("created", detail, str(claimed.id))
