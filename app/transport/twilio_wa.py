"""Twilio WhatsApp wire format — parse inbound webhooks and send text.

Phone numbers are normalized to digits-only (no ``+``, no ``whatsapp:``)
so contacts stay compatible with existing Z-API-era rows.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from twilio.base.exceptions import TwilioRestException
from twilio.request_validator import RequestValidator
from twilio.rest import Client

from app.core.config import settings
from app.core.logutil import get_logger

log = get_logger(__name__)

CUSTOMER_SERVICE_WINDOW = timedelta(hours=24)

PERMANENT_TWILIO_ERROR_CODES = frozenset({
    63049,  # US Marketing opt-in required (Meta policy)
    63016,  # Outside 24hr customer service window (template required)
    63001,  # Channel not found
    63002,  # Content template not found
    63003,  # Content template translation not found
    63005,  # Parameter count mismatch
    63007,  # Variable validation failed
    63015,  # Template rejected by Meta/WhatsApp
    63024,  # Media unsupported
    21211,  # Invalid 'To' phone number
    21614,  # Not a valid mobile number
    21610,  # Recipient unsubscribed (STOP)
})


# True when Twilio says "don't retry this" (bad number, template rejected, etc.).
def is_permanent_twilio_rejection(exc: Exception) -> bool:
    """True when Twilio/Meta returned an unrecoverable rejection that must not be retried."""
    if isinstance(exc, TwilioRestException):
        code = getattr(exc, "code", None)
        status = getattr(exc, "status", None)
        if code in PERMANENT_TWILIO_ERROR_CODES:
            return True
        if status is not None and 400 <= status < 500 and status != 429:
            return True
    return False


# True when the error is temporary (timeout, 5xx, rate limit) — safe to retry.
def is_transient_twilio_error(exc: Exception) -> bool:
    """True when failure is transient (network timeout, 5xx server error, 429 rate limit)."""
    if isinstance(exc, TwilioRestException):
        status = getattr(exc, "status", None)
        code = getattr(exc, "code", None)
        if status in (429, 500, 502, 503, 504) or code == 20429:
            return True
        if is_permanent_twilio_rejection(exc):
            return False
    return isinstance(exc, (TimeoutError, asyncio.TimeoutError, ConnectionError, OSError))


@dataclass(frozen=True)
class InboundMessage:
    phone: str
    body: str
    provider_message_id: str
    sender_name: str | None
    account_sid: str | None = None
    from_address: str | None = None
    to_address: str | None = None
    num_media: int | None = None
    media_url: str | None = None
    media_content_type: str | None = None
    wa_id: str | None = None
    sms_status: str | None = None
    api_version: str | None = None
    num_segments: int | None = None
    profile_name: str | None = None


# Normalize a phone string to digits only (strips whatsapp: and +).
def _digits_phone(value: str) -> str | None:
    """Strip ``whatsapp:`` / ``+`` / non-digits → digits-only E.164 body."""
    raw = value.strip()
    if raw.lower().startswith("whatsapp:"):
        raw = raw.split(":", 1)[1]
    digits = "".join(ch for ch in raw if ch.isdigit())
    return digits or None


# Format digits as a Twilio WhatsApp address (whatsapp:+E164).
def _whatsapp_address(phone_digits: str) -> str:
    return f"whatsapp:+{phone_digits}"


# Read an optional string field from a Twilio webhook form.
def _optional_str(form: dict[str, Any], key: str) -> str | None:
    value = form.get(key)
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned or None


# Read an optional integer field from a Twilio webhook form.
def _optional_int(form: dict[str, Any], key: str) -> int | None:
    value = form.get(key)
    if value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def whatsapp_from_address() -> str:
    """Configured Twilio sender as a ``whatsapp:+E.164`` address."""
    raw = settings.twilio_whatsapp_from.strip()
    digits = _digits_phone(raw)
    if digits is None:
        raise RuntimeError(
            f"TWILIO_WHATSAPP_FROM is not a phone number: {raw!r}"
        )
    return _whatsapp_address(digits)


def parse_inbound(form: dict[str, Any]) -> InboundMessage | None:
    """Parse a Twilio Messaging webhook form into a normalized inbound message.

    Accepts text and media. Drops silently: missing ids, non-WhatsApp senders,
    and messages with neither a body nor media.
    """
    from_raw = form.get("From")
    if not isinstance(from_raw, str) or not from_raw:
        return None
    if not from_raw.lower().startswith("whatsapp:"):
        return None

    phone = _digits_phone(from_raw)
    if phone is None:
        return None

    message_sid = form.get("MessageSid") or form.get("SmsSid")
    if not isinstance(message_sid, str) or not message_sid:
        return None

    body_raw = form.get("Body")
    body = body_raw.strip() if isinstance(body_raw, str) else ""

    num_media = _optional_int(form, "NumMedia")
    media_url = _optional_str(form, "MediaUrl0")
    media_content_type = _optional_str(form, "MediaContentType0")
    has_media = (num_media is not None and num_media > 0) or media_url is not None
    if not body and not has_media:
        return None

    profile_name = _optional_str(form, "ProfileName")

    return InboundMessage(
        phone=phone,
        body=body,
        provider_message_id=message_sid,
        sender_name=profile_name,
        account_sid=_optional_str(form, "AccountSid"),
        from_address=from_raw.strip(),
        to_address=_optional_str(form, "To"),
        num_media=num_media,
        media_url=media_url,
        media_content_type=media_content_type,
        wa_id=_optional_str(form, "WaId"),
        sms_status=_optional_str(form, "SmsStatus"),
        api_version=_optional_str(form, "ApiVersion"),
        num_segments=_optional_int(form, "NumSegments"),
        profile_name=profile_name,
    )


def validate_signature(
    *,
    url: str,
    params: dict[str, str],
    signature: str | None,
) -> bool:
    """Validate a Twilio webhook signature."""
    if not signature:
        return False
    return RequestValidator(settings.twilio_auth_token).validate(
        url, params, signature
    )


# Blocking Twilio SDK call to send a plain-text WhatsApp message.
def _create_message(phone_digits: str, message: str) -> str:
    client = Client(settings.twilio_account_sid, settings.twilio_auth_token)
    msg = client.messages.create(
        from_=whatsapp_from_address(),
        to=_whatsapp_address(phone_digits),
        body=message,
    )
    sid = msg.sid
    if not isinstance(sid, str) or not sid:
        raise RuntimeError(f"Twilio Messages missing sid: {msg!r}")
    return sid


async def send_text(phone: str, message: str) -> str:
    """Send plain text via the Twilio SDK. Returns the MessageSid."""
    phone_digits = _digits_phone(phone)
    if phone_digits is None:
        raise RuntimeError(f"invalid phone for Twilio send: {phone!r}")

    message_sid = await asyncio.to_thread(_create_message, phone_digits, message)

    log.info(
        "twilio_send_text",
        extra={
            "event": "twilio_send_text",
            "provider_message_id": message_sid,
        },
    )
    return message_sid


def in_customer_service_window(
    last_inbound: datetime | None,
    *,
    now: datetime | None = None,
) -> bool:
    """True when free-form WhatsApp text is allowed (24h since last inbound)."""
    if last_inbound is None:
        return False
    current = now or datetime.now(timezone.utc)
    if last_inbound.tzinfo is None:
        last_inbound = last_inbound.replace(tzinfo=timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current - last_inbound <= CUSTOMER_SERVICE_WINDOW


# Blocking Twilio SDK call to send an approved content template.
def _create_content_message(
    phone_digits: str,
    *,
    content_sid: str,
    variables: dict[str, str] | None,
) -> str:
    client = Client(settings.twilio_account_sid, settings.twilio_auth_token)
    kwargs: dict[str, Any] = {
        "from_": whatsapp_from_address(),
        "to": _whatsapp_address(phone_digits),
        "content_sid": content_sid,
    }
    if variables is not None:
        kwargs["content_variables"] = json.dumps(variables)
    msg = client.messages.create(**kwargs)
    sid = msg.sid
    if not isinstance(sid, str) or not sid:
        raise RuntimeError(f"Twilio Messages missing sid: {msg!r}")
    return sid


async def send_content_template(
    phone: str,
    *,
    content_sid: str,
    body_variable: str | None = None,
    variables: dict[str, str] | None = None,
) -> str:
    """Send an approved Content Template.

    Reminders pass ``body_variable`` (maps to ``{{1}}``). Omit both for a
    static template. ``variables`` wins when given explicitly.
    """
    phone_digits = _digits_phone(phone)
    if phone_digits is None:
        raise RuntimeError(f"invalid phone for Twilio send: {phone!r}")

    if variables is not None:
        payload: dict[str, str] | None = variables
    elif body_variable is not None:
        payload = {"1": body_variable}
    else:
        payload = None

    try:
        message_sid = await asyncio.to_thread(
            _create_content_message,
            phone_digits,
            content_sid=content_sid,
            variables=payload,
        )
    except TwilioRestException as exc:
        log.warning(
            "twilio_send_content_template_failed",
            extra={
                "event": "twilio_send_content_template_failed",
                "content_sid": content_sid,
                "error_code": getattr(exc, "code", None),
                "status": getattr(exc, "status", None),
            },
        )
        raise

    log.info(
        "twilio_send_content",
        extra={
            "event": "twilio_send_content",
            "provider_message_id": message_sid,
            "content_sid": content_sid,
        },
    )
    return message_sid


async def send_reminder_template(phone: str, body: str) -> str:
    """Send the approved WhatsApp utility template for deterministic reminder ping."""
    sid = settings.twilio_reminder_content_sid
    if not sid:
        raise RuntimeError("TWILIO_REMINDER_CONTENT_SID is not configured")
    return await send_content_template(phone, content_sid=sid, body_variable=body)


async def send_automation_template(phone: str, summary: str) -> str:
    """Send the approved WhatsApp utility template for proactive automation completion."""
    sid = settings.twilio_automation_content_sid or settings.twilio_reminder_content_sid
    if not sid:
        raise RuntimeError("TWILIO_AUTOMATION_CONTENT_SID is not configured")
    return await send_content_template(
        phone, content_sid=sid, variables={"1": summary}
    )


async def send_action_template(phone: str, action_summary: str) -> str:
    """Send the approved WhatsApp utility template for action-needs-response confirmation."""
    sid = (
        settings.twilio_action_content_sid
        or settings.twilio_automation_content_sid
        or settings.twilio_reminder_content_sid
    )
    if not sid:
        raise RuntimeError("TWILIO_ACTION_CONTENT_SID is not configured")
    return await send_content_template(
        phone, content_sid=sid, variables={"1": action_summary}
    )
