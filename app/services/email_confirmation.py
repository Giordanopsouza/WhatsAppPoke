"""Claim and execute a Gmail draft selected by Interaction confirmation."""

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
from app.integrations.composio_proxy import AuthenticatedProxyAdapter, AuthenticatedProxyError
from app.integrations.gmail import GmailPayloadError, send_draft_request


ConfirmationState = Literal[
    "none", "ambiguous", "sent", "retryable_failure", "invalid"
]


@dataclass(frozen=True)
class EmailConfirmationOutcome:
    state: ConfirmationState
    detail: str
    action_id: str | None = None


# Pick which staged email the user is confirming (by id or if only one exists).
def _select_email_action(
    rows: list[PendingAction], action_id: uuid.UUID | None
) -> PendingAction | None:
    if action_id is not None:
        return next(
            (
                row
                for row in rows
                if row.id == action_id and row.kind == PendingActionKind.SEND_EMAIL
            ),
            None,
        )
    email_rows = [row for row in rows if row.kind == PendingActionKind.SEND_EMAIL]
    if len(email_rows) == 1:
        return email_rows[0]
    return None


# User confirmed a staged email — claim it and send via Gmail.
async def confirm_staged_email(
    *,
    contact_id: int,
    inbound_turn_id: str,
    session_factory: async_sessionmaker[AsyncSession],
    action_id: uuid.UUID | None = None,
    proxy: AuthenticatedProxyAdapter | None = None,
) -> EmailConfirmationOutcome:
    """Claim and send one fixed draft selected by the Interaction Agent."""
    async with session_factory() as session:
        candidates = await list_pending_actions(session, contact_id=contact_id)
        selected = _select_email_action(candidates, action_id)
        if not candidates:
            return EmailConfirmationOutcome("none", "não há ação pendente válida")
        if selected is None:
            return EmailConfirmationOutcome(
                "ambiguous", "há mais de uma ação pendente; pergunte qual deve ser confirmada"
            )
        expected_hash = payload_hash(selected.payload)
        if expected_hash != selected.payload_hash:
            claimed = await claim_pending_action(
                session,
                contact_id=contact_id,
                kind=PendingActionKind.SEND_EMAIL,
                turn_id=inbound_turn_id,
                action_id=selected.id,
                payload_hash=selected.payload_hash,
            )
            if claimed is not None:
                await fail_pending_action(session, action_id=claimed.id)
                await session.commit()
            return EmailConfirmationOutcome("invalid", "ação pendente inválida")
        claimed = await claim_pending_action(
            session,
            contact_id=contact_id,
            kind=PendingActionKind.SEND_EMAIL,
            turn_id=inbound_turn_id,
            action_id=selected.id,
            payload_hash=expected_hash,
        )
        await session.commit()

    if claimed is None:
        return EmailConfirmationOutcome("none", "ação expirada, já usada ou criada neste turno")
    draft_id = claimed.payload.get("draft_id")
    if not isinstance(draft_id, str):
        async with session_factory() as session:
            await fail_pending_action(session, action_id=claimed.id)
            await session.commit()
        return EmailConfirmationOutcome("invalid", "ação pendente inválida", str(claimed.id))

    async with session_factory() as session:
        integrations = await list_active_integrations(session, contact_id=contact_id)
    integration = next((row for row in integrations if row.provider == "gmail"), None)
    if integration is None:
        async with session_factory() as session:
            await release_pending_action(session, action_id=claimed.id)
            await session.commit()
        return EmailConfirmationOutcome(
            "retryable_failure", "Gmail não está conectado", str(claimed.id)
        )

    adapter = proxy or AuthenticatedProxyAdapter(toolkit="gmail")
    try:
        response = await asyncio.to_thread(
            adapter.execute,
            contact_id=contact_id,
            integration=integration,
            owned_tool_name="execute_confirmed_email_send",
            request=send_draft_request(draft_id),
        )
    except (AuthenticatedProxyError, GmailPayloadError):
        async with session_factory() as session:
            await release_pending_action(session, action_id=claimed.id)
            await session.commit()
        return EmailConfirmationOutcome(
            "retryable_failure", "não consegui enviar agora; a confirmação pode ser tentada de novo",
            str(claimed.id),
        )

    async with session_factory() as session:
        await discard_pending_action(session, action_id=claimed.id)
        await session.commit()
    provider_id = response.data.get("id") if isinstance(response.data, dict) else None
    detail = "e-mail enviado"
    if isinstance(provider_id, str):
        detail += f"; message_id={provider_id[:256]}"
    return EmailConfirmationOutcome("sent", detail, str(claimed.id))
