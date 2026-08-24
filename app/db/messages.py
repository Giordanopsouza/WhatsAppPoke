"""Message insert, history load, and idempotency checks."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Message, MessageDirection
from app.db.session import HISTORY_LIMIT, HISTORY_MAX_CHARS


async def insert_inbound_message(
    session: AsyncSession,
    *,
    contact_id: int,
    body: str,
    provider_message_id: str,
    account_sid: str | None = None,
    from_address: str | None = None,
    to_address: str | None = None,
    num_media: int | None = None,
    media_url: str | None = None,
    media_content_type: str | None = None,
    wa_id: str | None = None,
    sms_status: str | None = None,
    api_version: str | None = None,
    num_segments: int | None = None,
    profile_name: str | None = None,
) -> bool:
    """Insert an inbound message.

    Returns False when ``provider_message_id`` already exists (duplicate delivery).
    """
    stmt = insert(Message).values(
        contact_id=contact_id,
        direction=MessageDirection.IN,
        body=body,
        provider_message_id=provider_message_id,
        account_sid=account_sid,
        from_address=from_address,
        to_address=to_address,
        num_media=num_media,
        media_url=media_url,
        media_content_type=media_content_type,
        wa_id=wa_id,
        sms_status=sms_status,
        api_version=api_version,
        num_segments=num_segments,
        profile_name=profile_name,
    )
    try:
        async with session.begin_nested():
            await session.execute(stmt)
    except IntegrityError:
        return False
    return True


async def insert_outbound_message(
    session: AsyncSession,
    *,
    contact_id: int,
    body: str,
    provider_message_id: str,
) -> None:
    """Persist a reply after a successful Twilio send."""
    stmt = insert(Message).values(
        contact_id=contact_id,
        direction=MessageDirection.OUT,
        body=body,
        provider_message_id=provider_message_id,
    )
    await session.execute(stmt)


async def outbound_exists_since(
    session: AsyncSession,
    *,
    contact_id: int,
    body: str,
    since: datetime,
) -> bool:
    """True if this exact reply was already sent to ``contact_id`` after ``since``.

    Send-then-persist has a window: the WhatsApp send can succeed and the
    insert fail, after which the job is retried. Callers use this to avoid
    sending the same fixed message twice.
    """
    stmt = (
        select(Message.id)
        .where(
            Message.contact_id == contact_id,
            Message.direction == MessageDirection.OUT,
            Message.body == body,
            Message.created_at >= since,
        )
        .limit(1)
    )
    return await session.scalar(stmt) is not None


async def load_recent_messages(
    session: AsyncSession,
    contact_id: int,
    *,
    limit: int = HISTORY_LIMIT,
    max_chars: int = HISTORY_MAX_CHARS,
) -> list[dict[str, str]]:
    """Load recent chat turns for one contact, oldest → newest.

    Scoped strictly by ``contact_id``. Caps at ``limit`` rows and ~``max_chars``
    of body text (drops oldest first; always keeps the newest non-empty
    message). Empty bodies (media-only inbound) are omitted so they cannot
    become the LLM prompt; media columns stay on the row for a future
    vision feature.
    """
    stmt = (
        select(Message.direction, Message.body)
        .where(
            Message.contact_id == contact_id,
            func.trim(Message.body) != "",
        )
        .order_by(Message.created_at.desc())
        .limit(limit)
    )
    rows = list(await session.execute(stmt))
    rows.reverse()

    messages: list[dict[str, str]] = [
        {
            "role": "user" if direction == MessageDirection.IN else "assistant",
            "content": body,
        }
        for direction, body in rows
    ]

    total = sum(len(m["content"]) for m in messages)
    while len(messages) > 1 and total > max_chars:
        dropped = messages.pop(0)
        total -= len(dropped["content"])

    return messages


async def last_inbound_at(
    session: AsyncSession,
    *,
    contact_id: int,
) -> datetime | None:
    """Newest inbound message timestamp (for the 24h customer-service window)."""
    stmt = (
        select(Message.created_at)
        .where(
            Message.contact_id == contact_id,
            Message.direction == MessageDirection.IN,
        )
        .order_by(Message.created_at.desc())
        .limit(1)
    )
    return await session.scalar(stmt)
