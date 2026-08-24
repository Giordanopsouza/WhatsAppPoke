"""Integration notify job handler."""

from __future__ import annotations

from typing import Any

from app.database.models import Contact, Job
from app.db import (
    contact_interaction_lock,
    get_session,
    insert_outbound_message,
    outbound_exists_since,
)
from app.integrations.providers import UnknownProvider, get_provider
from app.transport.twilio_wa import send_text
from app.worker._helpers import contact_id, log

INTEGRATION_NOTIFY_REPLY = "Conectado ✓ volta pro WhatsApp."


# Build the WhatsApp confirmation text after a successful OAuth connect.
def integration_notify_body(payload: dict[str, Any] | None) -> str:
    """WhatsApp confirmation copy for a successful connect (registry PT)."""
    provider = (payload or {}).get("provider")
    if isinstance(provider, str) and provider:
        try:
            return get_provider(provider).notify_body_pt
        except UnknownProvider:
            pass
    return INTEGRATION_NOTIFY_REPLY


async def handle_integration_notify(job: Job) -> None:
    """Send the WhatsApp confirmation after a successful OAuth connect."""
    async with contact_interaction_lock(contact_id(job)):
        await _run_integration_notify(job)


# Actual work for an integration_notify job (called under the contact lock).
async def _run_integration_notify(job: Job) -> None:
    body = integration_notify_body(job.payload)
    async with get_session() as session:
        contact = await session.get(Contact, job.contact_id)
        if contact is None:
            raise RuntimeError(
                f"contact {job.contact_id} missing for job {job.id}"
            )
        phone = contact.phone
        already_sent = await outbound_exists_since(
            session,
            contact_id=job.contact_id,
            body=body,
            since=job.created_at,
        )

    if already_sent:
        log.info(
            "integration_notify_already_sent",
            extra={
                "event": "integration_notify_already_sent",
                "job_id": str(job.id),
                "contact_id": job.contact_id,
            },
        )
        return

    out_id = await send_text(phone, body)

    async with get_session() as session:
        await insert_outbound_message(
            session,
            contact_id=job.contact_id,
            body=body,
            provider_message_id=out_id,
        )
        await session.commit()

    log.info(
        "integration_notify_sent",
        extra={
            "event": "integration_notify_sent",
            "job_id": str(job.id),
            "contact_id": job.contact_id,
            "provider": (job.payload or {}).get("provider"),
        },
    )
