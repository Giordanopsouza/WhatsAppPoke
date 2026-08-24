"""Worker handler tests: reply-once guards and integration_notify delivery."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

from app.database.models import JobKind, ReminderStatus
from app.worker.handlers import (
    integration_notify,
    reminder_due,
)


def _job(*, attempts: int = 0, contact_id: int = 7) -> MagicMock:
    job = MagicMock()
    job.id = uuid.uuid4()
    job.contact_id = contact_id
    job.attempts = attempts
    job.payload = {"provider_message_id": "wamid.1"}
    job.created_at = datetime.now(timezone.utc)
    return job


def _reminder_job(*, reminder_id: uuid.UUID | None = None, contact_id: int = 7) -> MagicMock:
    job = _job(contact_id=contact_id)
    job.kind = JobKind.REMINDER_DUE
    job.payload = {"reminder_id": str(reminder_id or uuid.uuid4())}
    return job


def _session_patch(module: Any, session: Any):
    @asynccontextmanager
    async def _fake() -> AsyncIterator[Any]:
        yield session

    return patch.object(module, "get_session", _fake)


def _contact_session(phone: str = "5511999887766") -> AsyncMock:
    session = AsyncMock()
    session.get.return_value = MagicMock(phone=phone)
    return session


async def test_integration_notify_sends_the_confirmation() -> None:
    job = _job()
    job.payload = {"provider": "unknown-toolkit"}

    with (
        _session_patch(integration_notify, _contact_session()),
        patch.object(integration_notify, "outbound_exists_since", AsyncMock(return_value=False)),
        patch.object(integration_notify, "send_text", AsyncMock(return_value="out-9")) as send,
        patch.object(integration_notify, "insert_outbound_message", AsyncMock()) as persist,
    ):
        await integration_notify._run_integration_notify(job)

    send.assert_awaited_once_with("5511999887766", integration_notify.INTEGRATION_NOTIFY_REPLY)
    persist.assert_awaited_once()


async def test_integration_notify_uses_registry_copy_for_provider() -> None:
    job = _job()
    job.payload = {"provider": "notion"}
    body = integration_notify.integration_notify_body(job.payload)
    assert "Notion" in body

    with (
        _session_patch(integration_notify, _contact_session()),
        patch.object(integration_notify, "outbound_exists_since", AsyncMock(return_value=False)),
        patch.object(integration_notify, "send_text", AsyncMock(return_value="out-9")) as send,
        patch.object(integration_notify, "insert_outbound_message", AsyncMock()) as persist,
    ):
        await integration_notify._run_integration_notify(job)

    send.assert_awaited_once_with("5511999887766", body)
    assert persist.await_args.kwargs["body"] == body


async def test_integration_notify_retry_does_not_send_twice() -> None:
    """Send succeeded, persist failed, job retried — do not re-send."""
    job = _job(attempts=1)
    job.payload = {"provider": "notion"}
    body = integration_notify.integration_notify_body(job.payload)

    with (
        _session_patch(integration_notify, _contact_session()),
        patch.object(
            integration_notify, "outbound_exists_since", AsyncMock(return_value=True)
        ) as exists,
        patch.object(integration_notify, "send_text", AsyncMock()) as send,
    ):
        await integration_notify._run_integration_notify(job)

    exists.assert_awaited_once()
    assert exists.await_args.kwargs["body"] == body
    send.assert_not_awaited()


def _reminder_session(
    *,
    reminder: MagicMock | None,
    phone: str = "5511999887766",
    tz: str = "America/Sao_Paulo",
    claimed: MagicMock | None = None,
) -> AsyncMock:
    session = AsyncMock()
    contact = MagicMock(phone=phone, tz=tz)

    async def _get(model: Any, key: Any = None, **kwargs: Any) -> Any:
        name = getattr(model, "__name__", str(model))
        if name == "Contact":
            return contact
        return None

    session.get.side_effect = _get
    return session


def test_format_reminder_ping_is_stored_body() -> None:
    assert reminder_due.format_reminder_ping("  alongar  ") == "alongar"


async def test_reminder_due_noop_when_cancelled() -> None:
    reminder_id = uuid.uuid4()
    job = _reminder_job(reminder_id=reminder_id)
    cancelled = MagicMock(status=ReminderStatus.CANCELLED, body="x")
    session = _reminder_session(reminder=cancelled)

    with (
        _session_patch(reminder_due, session),
        patch.object(reminder_due, "get_reminder", AsyncMock(return_value=cancelled)),
        patch.object(reminder_due, "claim_reminder_for_send", AsyncMock()) as claim,
        patch.object(reminder_due, "send_text", AsyncMock()) as send_text,
        patch.object(reminder_due, "send_content_template", AsyncMock()) as send_tmpl,
    ):
        await reminder_due._run_reminder_due(job)

    claim.assert_not_awaited()
    send_text.assert_not_awaited()
    send_tmpl.assert_not_awaited()


async def test_reminder_due_noop_when_already_sent() -> None:
    reminder_id = uuid.uuid4()
    job = _reminder_job(reminder_id=reminder_id)
    sent = MagicMock(status=ReminderStatus.SENT, body="x")

    with (
        _session_patch(reminder_due, _reminder_session(reminder=sent)),
        patch.object(reminder_due, "get_reminder", AsyncMock(return_value=sent)),
        patch.object(reminder_due, "send_text", AsyncMock()) as send_text,
        patch.object(reminder_due, "send_content_template", AsyncMock()) as send_tmpl,
    ):
        await reminder_due._run_reminder_due(job)

    send_text.assert_not_awaited()
    send_tmpl.assert_not_awaited()


async def test_reminder_due_sends_free_form_in_window() -> None:
    reminder_id = uuid.uuid4()
    job = _reminder_job(reminder_id=reminder_id)
    active = MagicMock(
        status=ReminderStatus.ACTIVE,
        body="alongar",
        id=reminder_id,
    )
    claimed = MagicMock(status=ReminderStatus.SENT, body="alongar", id=reminder_id)
    inbound = datetime.now(timezone.utc) - timedelta(hours=1)
    ping = reminder_due.format_reminder_ping("alongar")

    with (
        _session_patch(reminder_due, _reminder_session(reminder=active)),
        patch.object(reminder_due, "get_reminder", AsyncMock(return_value=active)),
        patch.object(reminder_due, "last_inbound_at", AsyncMock(return_value=inbound)),
        patch.object(reminder_due, "claim_reminder_for_send", AsyncMock(return_value=claimed)),
        patch.object(reminder_due, "send_text", AsyncMock(return_value="SM123")) as send_text,
        patch.object(reminder_due, "send_content_template", AsyncMock()) as send_tmpl,
        patch.object(reminder_due, "insert_outbound_message", AsyncMock()) as persist,
    ):
        await reminder_due._run_reminder_due(job)

    send_text.assert_awaited_once_with("5511999887766", ping)
    send_tmpl.assert_not_awaited()
    persist.assert_awaited_once()
    assert persist.await_args.kwargs["body"] == ping


async def test_reminder_due_uses_template_out_of_window() -> None:
    reminder_id = uuid.uuid4()
    job = _reminder_job(reminder_id=reminder_id)
    active = MagicMock(
        status=ReminderStatus.ACTIVE,
        body="alongar",
        id=reminder_id,
    )
    claimed = MagicMock(status=ReminderStatus.SENT, body="alongar", id=reminder_id)
    inbound = datetime.now(timezone.utc) - timedelta(hours=30)
    ping = reminder_due.format_reminder_ping("alongar")

    with (
        _session_patch(reminder_due, _reminder_session(reminder=active)),
        patch.object(reminder_due, "get_reminder", AsyncMock(return_value=active)),
        patch.object(reminder_due, "last_inbound_at", AsyncMock(return_value=inbound)),
        patch.object(reminder_due, "claim_reminder_for_send", AsyncMock(return_value=claimed)),
        patch.object(reminder_due, "send_text", AsyncMock()) as send_text,
        patch.object(
            reminder_due, "send_content_template", AsyncMock(return_value="SM999")
        ) as send_tmpl,
        patch.object(reminder_due, "insert_outbound_message", AsyncMock()),
    ):
        await reminder_due._run_reminder_due(job)

    send_text.assert_not_awaited()
    send_tmpl.assert_awaited_once()
    assert send_tmpl.await_args.kwargs["body_variable"] == ping


async def test_reminder_due_claim_race_skips_send() -> None:
    """Another worker claimed first — do not send."""
    reminder_id = uuid.uuid4()
    job = _reminder_job(reminder_id=reminder_id)
    active = MagicMock(status=ReminderStatus.ACTIVE, body="x", id=reminder_id)

    with (
        _session_patch(reminder_due, _reminder_session(reminder=active)),
        patch.object(reminder_due, "get_reminder", AsyncMock(return_value=active)),
        patch.object(reminder_due, "last_inbound_at", AsyncMock(return_value=None)),
        patch.object(reminder_due, "claim_reminder_for_send", AsyncMock(return_value=None)),
        patch.object(reminder_due, "send_text", AsyncMock()) as send_text,
        patch.object(reminder_due, "send_content_template", AsyncMock()) as send_tmpl,
    ):
        await reminder_due._run_reminder_due(job)

    send_text.assert_not_awaited()
    send_tmpl.assert_not_awaited()
