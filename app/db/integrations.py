"""Connect links, integrations, and pending actions."""

from __future__ import annotations

import uuid
from hashlib import sha256
import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import (
    ConnectLink,
    Integration,
    IntegrationStatus,
    PendingAction,
    PendingActionKind,
    PendingActionStatus,
)
from app.db.session import CONNECT_CONSENT_TTL, CONNECT_LINK_TTL, PENDING_ACTION_TTL


async def create_connect_link(
    session: AsyncSession,
    *,
    contact_id: int,
    nonce: str,
    provider: str,
) -> ConnectLink:
    """Persist a one-time connect link (10-minute expiry)."""
    link = ConnectLink(
        contact_id=contact_id,
        nonce=nonce,
        provider=provider,
        expires_at=datetime.now(timezone.utc) + CONNECT_LINK_TTL,
    )
    session.add(link)
    await session.flush()
    return link


async def get_usable_connect_link(
    session: AsyncSession,
    *,
    nonce: str,
) -> ConnectLink | None:
    """Return an unused, unexpired connect_link for ``nonce``, else None."""
    link = await session.scalar(
        select(ConnectLink).where(ConnectLink.nonce == nonce)
    )
    if link is None:
        return None
    if link.used_at is not None:
        return None
    expires = link.expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if expires <= datetime.now(timezone.utc):
        return None
    return link


async def start_connect_link(
    session: AsyncSession,
    *,
    nonce: str,
) -> ConnectLink | None:
    """Begin the consent leg: verify the link is usable and extend its expiry.

    The user is about to leave for Google's consent screen, which can easily
    outlive the 10-minute minting window (Testing mode adds the "unverified
    app" interstitial). Extending here means the clock covers the consent leg
    instead of expiring an authorization the user already granted.
    """
    stmt = (
        update(ConnectLink)
        .where(
            ConnectLink.nonce == nonce,
            ConnectLink.used_at.is_(None),
            ConnectLink.expires_at > func.now(),
        )
        .values(
            expires_at=datetime.now(timezone.utc) + CONNECT_CONSENT_TTL,
            updated_at=func.now(),
        )
        .returning(ConnectLink)
    )
    result = await session.scalars(
        stmt, execution_options={"populate_existing": True}
    )
    return result.one_or_none()


async def claim_connect_link(
    session: AsyncSession,
    *,
    nonce: str,
) -> ConnectLink | None:
    """Atomically consume a one-time link. Returns None if used/expired/unknown.

    A single UPDATE does the check and the write, so two callbacks firing at
    once (double tap, in-app-browser retry) cannot both proceed to exchange the
    same Google authorization code — Google revokes the tokens it just issued
    when a code is replayed.
    """
    stmt = (
        update(ConnectLink)
        .where(
            ConnectLink.nonce == nonce,
            ConnectLink.used_at.is_(None),
            ConnectLink.expires_at > func.now(),
        )
        .values(used_at=func.now(), updated_at=func.now())
        .returning(ConnectLink)
    )
    result = await session.scalars(
        stmt, execution_options={"populate_existing": True}
    )
    return result.one_or_none()


async def list_active_integrations(
    session: AsyncSession,
    *,
    contact_id: int,
) -> list[Integration]:
    """Return active integration rows for a contact (any provider)."""
    result = await session.scalars(
        select(Integration)
        .where(
            Integration.contact_id == contact_id,
            Integration.status == IntegrationStatus.ACTIVE,
        )
        .order_by(Integration.provider)
    )
    return list(result.all())


async def upsert_integration(
    session: AsyncSession,
    *,
    contact_id: int,
    provider: str,
    external_account_id: str | None,
) -> Integration:
    """Insert or update a Composio connected-account pointer for contact+provider."""
    stmt = (
        insert(Integration)
        .values(
            contact_id=contact_id,
            provider=provider,
            external_account_id=external_account_id,
            status=IntegrationStatus.ACTIVE,
        )
        .on_conflict_do_update(
            constraint="uq_integration_contact_provider",
            set_={
                "external_account_id": external_account_id,
                "status": IntegrationStatus.ACTIVE,
                "updated_at": func.now(),
            },
        )
        .returning(Integration)
    )
    return (
        await session.scalars(stmt, execution_options={"populate_existing": True})
    ).one()


async def create_pending_action(
    session: AsyncSession,
    *,
    contact_id: int,
    kind: PendingActionKind,
    payload: dict[str, Any],
    turn_id: str,
    source_interaction_run_id: uuid.UUID | None = None,
    source_execution_run_id: uuid.UUID | None = None,
) -> PendingAction:
    """Stage a write action for later confirmation (15-minute expiry).

    Cancels an older proposal of the same kind first. Different kinds may be
    pending together; the confirmation boundary must disambiguate a bare
    “sim” before claiming either one.
    """
    await session.execute(
        update(PendingAction)
        .where(
            PendingAction.contact_id == contact_id,
            PendingAction.kind == kind,
            PendingAction.status.in_(
                (PendingActionStatus.PENDING, PendingActionStatus.CLAIMED)
            ),
        )
        .values(status=PendingActionStatus.CANCELLED, updated_at=func.now())
    )
    row = PendingAction(
        contact_id=contact_id,
        kind=kind,
        payload=payload,
        payload_hash=_payload_hash(payload),
        status=PendingActionStatus.PENDING,
        created_turn_id=turn_id,
        source_interaction_run_id=source_interaction_run_id,
        source_execution_run_id=source_execution_run_id,
        expires_at=datetime.now(timezone.utc) + PENDING_ACTION_TTL,
    )
    session.add(row)
    await session.flush()
    return row


async def claim_pending_action(
    session: AsyncSession,
    *,
    contact_id: int,
    kind: PendingActionKind,
    turn_id: str,
    action_id: uuid.UUID | None = None,
    payload_hash: str | None = None,
) -> PendingAction | None:
    """Atomically take this contact's staged action of ``kind`` for execution.

    UPDATE … RETURNING (not DELETE): the row survives until the Google call
    succeeds, so a failure can release it and the person's next “sim” still
    works. Only ``pending`` rows match, so a double “sim” across concurrent
    turns cannot execute the same payload twice.

    ``created_turn_id != turn_id`` is the confirmation gate: the model can call
    propose and confirm in one run, and this is what stops that run from
    sending. Confirmation must arrive as a separate WhatsApp message.
    """
    candidate = select(PendingAction.id).where(
            PendingAction.contact_id == contact_id,
            PendingAction.kind == kind,
            PendingAction.status == PendingActionStatus.PENDING,
            PendingAction.expires_at > func.now(),
            PendingAction.created_turn_id.is_distinct_from(turn_id),
        )
    if action_id is not None:
        candidate = candidate.where(PendingAction.id == action_id)
    if payload_hash is not None:
        candidate = candidate.where(PendingAction.payload_hash == payload_hash)
    newest_id = candidate.order_by(PendingAction.created_at.desc()).limit(1).scalar_subquery()
    stmt = (
        update(PendingAction)
        .where(
            PendingAction.id == newest_id,
            PendingAction.status == PendingActionStatus.PENDING,
        )
        .values(status=PendingActionStatus.CLAIMED, updated_at=func.now())
        .returning(PendingAction)
    )
    return await session.scalar(
        stmt, execution_options={"synchronize_session": False}
    )


async def list_pending_actions(
    session: AsyncSession,
    *,
    contact_id: int,
) -> list[PendingAction]:
    """List this contact's unexpired proposals for confirmation routing."""
    result = await session.scalars(
        select(PendingAction)
        .where(
            PendingAction.contact_id == contact_id,
            PendingAction.status == PendingActionStatus.PENDING,
            PendingAction.expires_at > func.now(),
        )
        .order_by(PendingAction.created_at.desc())
    )
    return list(result.all())


async def discard_pending_action(
    session: AsyncSession,
    *,
    action_id: uuid.UUID,
) -> None:
    """Record a claimed action as executed rather than deleting audit state."""
    await session.execute(
        update(PendingAction)
        .where(
            PendingAction.id == action_id,
            PendingAction.status == PendingActionStatus.CLAIMED,
        )
        .values(status=PendingActionStatus.EXECUTED, updated_at=func.now())
    )


async def release_pending_action(
    session: AsyncSession,
    *,
    action_id: uuid.UUID,
) -> None:
    """Put a claimed action back so the person's next “sim” can retry it.

    Keeps the original ``expires_at`` — a released action still dies at the
    15-minute mark rather than getting a fresh window on every failure.
    """
    await session.execute(
        update(PendingAction)
        .where(
            PendingAction.id == action_id,
            PendingAction.status == PendingActionStatus.CLAIMED,
        )
        .values(status=PendingActionStatus.PENDING, updated_at=func.now())
        .execution_options(synchronize_session=False)
    )


async def fail_pending_action(
    session: AsyncSession,
    *,
    action_id: uuid.UUID,
) -> None:
    """Record a terminal execution failure without making it confirmable again."""
    await session.execute(
        update(PendingAction)
        .where(
            PendingAction.id == action_id,
            PendingAction.status == PendingActionStatus.CLAIMED,
        )
        .values(status=PendingActionStatus.FAILED, updated_at=func.now())
        .execution_options(synchronize_session=False)
    )


async def expire_pending_actions(session: AsyncSession) -> int:
    """Terminally expire unclaimed proposals while preserving their audit trail."""
    result = await session.execute(
        update(PendingAction)
        .where(
            PendingAction.status == PendingActionStatus.PENDING,
            PendingAction.expires_at <= func.now(),
        )
        .values(status=PendingActionStatus.EXPIRED, updated_at=func.now())
    )
    return result.rowcount or 0


def payload_hash(payload: dict[str, Any]) -> str:
    """Hash the exact normalized payload retained for a sensitive write."""
    normalized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(normalized.encode()).hexdigest()


# Kept private as a compatibility alias for rows staged before task 043.
_payload_hash = payload_hash
