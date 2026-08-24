"""Reminder due job handler."""

from __future__ import annotations

import uuid
from typing import Any

import logfire

from app.core.config import settings
from app.database.models import Contact, Job, ReminderStatus
from app.db import (
    claim_reminder_for_send,
    contact_interaction_lock,
    get_reminder,
    get_session,
    insert_outbound_message,
    last_inbound_at,
    release_reminder_claim,
)
from app.transport.twilio_wa import (
    in_customer_service_window,
    send_content_template,
    send_text,
)
from app.worker._helpers import contact_id, log


# Format the reminder body as the WhatsApp ping text (no LLM).
def format_reminder_ping(body: str) -> str:
    """WhatsApp ping text from the stored reminder body (no LLM)."""
    return body.strip()


async def handle_reminder_due(job: Job) -> None:
    """Fire a scheduled reminder: lock → check row → format → send."""
    async with contact_interaction_lock(contact_id(job)):
        await _run_reminder_due(job)


# Actual work for a reminder_due job (called under the contact lock).
async def _run_reminder_due(job: Job) -> None:
    payload: dict[str, Any] = job.payload or {}
    raw_id = payload.get("reminder_id")
    if not isinstance(raw_id, str) or not raw_id:
        raise RuntimeError(f"reminder_due job {job.id} missing reminder_id")
    try:
        reminder_id = uuid.UUID(raw_id)
    except ValueError as exc:
        raise RuntimeError(
            f"reminder_due job {job.id} bad reminder_id: {raw_id!r}"
        ) from exc

    async with get_session() as session:
        reminder = await get_reminder(session, reminder_id=reminder_id)
        if reminder is None or reminder.status != ReminderStatus.ACTIVE:
            log.info(
                "reminder_due_noop",
                extra={
                    "event": "reminder_due_noop",
                    "job_id": str(job.id),
                    "contact_id": job.contact_id,
                    "reminder_id": str(reminder_id),
                    "status": None if reminder is None else str(reminder.status),
                },
            )
            return

        contact = await session.get(Contact, job.contact_id)
        if contact is None:
            raise RuntimeError(
                f"contact {job.contact_id} missing for job {job.id}"
            )
        phone = contact.phone
        body = reminder.body
        inbound_at = await last_inbound_at(
            session, contact_id=job.contact_id
        )
        claimed = await claim_reminder_for_send(
            session, reminder_id=reminder_id
        )
        await session.commit()

    if claimed is None:
        log.info(
            "reminder_due_already_claimed",
            extra={
                "event": "reminder_due_already_claimed",
                "job_id": str(job.id),
                "contact_id": job.contact_id,
                "reminder_id": str(reminder_id),
            },
        )
        return

    in_window = in_customer_service_window(inbound_at)
    reply = format_reminder_ping(body)
    out_id: str | None = None
    try:
        with logfire.span("reminder_send", contact_id=job.contact_id):
            if in_window:
                out_id = await send_text(phone, reply)
            else:
                out_id = await send_content_template(
                    phone,
                    content_sid=settings.twilio_reminder_content_sid,
                    body_variable=reply,
                )
        with logfire.span("reminder_persist", contact_id=job.contact_id):
            async with get_session() as session:
                await insert_outbound_message(
                    session,
                    contact_id=job.contact_id,
                    body=reply,
                    provider_message_id=out_id,
                )
                await session.commit()
    except Exception:
        if out_id is None:
            async with get_session() as session:
                await release_reminder_claim(
                    session, reminder_id=reminder_id
                )
                await session.commit()
        raise

    log.info(
        "reminder_due_sent",
        extra={
            "event": "reminder_due_sent",
            "job_id": str(job.id),
            "contact_id": job.contact_id,
            "reminder_id": str(reminder_id),
            "in_window": in_window,
            "provider_message_id": out_id,
        },
    )
