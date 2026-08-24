"""After-200 inbound dispatch for the sole Interaction conversation runtime."""

from __future__ import annotations

from app.agent.interaction import run_interaction_event
from app.core.logutil import get_logger


log = get_logger(__name__)


# Kick off Interaction after the webhook saved the inbound message.
async def dispatch_inbound(
    *,
    contact_id: int,
    phone: str,
    provider_message_id: str,
) -> None:
    """Run one Interaction event for a persisted inbound.

    This function never classifies, acknowledges, enqueues a conversation job,
    or sends WhatsApp itself. Interaction owns every visible response.
    """
    try:
        await run_interaction_event(
            contact_id=contact_id,
            phone=phone,
            provider_message_id=provider_message_id,
            internal_event_summary="nova mensagem recebida",
            event_kind="user_inbound",
        )
    except Exception:
        log.exception(
            "inbound_dispatch_failed",
            extra={
                "event": "inbound_dispatch_failed",
                "contact_id": contact_id,
                "provider_message_id": provider_message_id,
            },
        )
