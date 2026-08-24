import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.transport.twilio_wa import parse_inbound, send_content_template, send_text, whatsapp_from_address

FIXTURES = Path(__file__).parent / "fixtures"


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_parse_inbound_whatsapp_text():
    msg = parse_inbound(_load_fixture("twilio_inbound_text.json"))
    assert msg is not None
    assert msg.phone == "15551234567"
    assert msg.body == "Oi"
    assert msg.provider_message_id.startswith("SM")
    assert msg.sender_name == "Giordano"
    assert msg.profile_name == "Giordano"
    assert msg.account_sid == "ACaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    assert msg.from_address == "whatsapp:+15551234567"
    assert msg.to_address == "whatsapp:+14155238886"
    assert msg.num_media == 0
    assert msg.media_url is None
    assert msg.wa_id == "15551234567"
    assert msg.sms_status == "received"
    assert msg.api_version == "2010-04-01"
    assert msg.num_segments == 1


@pytest.mark.parametrize(
    ("fixture", "content_type"),
    [
        ("twilio_inbound_image.json", "image/jpeg"),
        ("twilio_inbound_audio.json", "audio/ogg"),
        ("twilio_inbound_sticker.json", "image/webp"),
    ],
)
def test_parse_inbound_media_empty_body(fixture: str, content_type: str):
    msg = parse_inbound(_load_fixture(fixture))
    assert msg is not None
    assert msg.body == ""
    assert msg.phone == "15551234567"
    assert msg.num_media == 1
    assert msg.media_url is not None
    assert msg.media_content_type == content_type
    assert msg.wa_id == "15551234567"


def test_parse_inbound_drops_sms_and_empty():
    assert parse_inbound({"From": "+16472447832", "Body": "x", "MessageSid": "SM1"}) is None
    assert (
        parse_inbound(
            {
                "From": "whatsapp:+16472447832",
                "Body": "   ",
                "MessageSid": "SM1",
                "NumMedia": "0",
            }
        )
        is None
    )


def test_whatsapp_from_address_normalizes(monkeypatch):
    monkeypatch.setattr(
        "app.transport.twilio_wa.settings.twilio_whatsapp_from",
        "+14155238886",
    )
    assert whatsapp_from_address() == "whatsapp:+14155238886"


@pytest.mark.asyncio
async def test_send_text_uses_twilio_sdk(monkeypatch):
    monkeypatch.setattr(
        "app.transport.twilio_wa.settings.twilio_account_sid",
        "ACaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    )
    monkeypatch.setattr(
        "app.transport.twilio_wa.settings.twilio_auth_token",
        "auth-token",
    )
    monkeypatch.setattr(
        "app.transport.twilio_wa.settings.twilio_whatsapp_from",
        "whatsapp:+14155238886",
    )

    created = MagicMock()
    created.sid = "SMffffffffffffffffffffffffffffffff"
    messages = MagicMock()
    messages.create.return_value = created
    client = MagicMock()
    client.messages = messages

    with patch("app.transport.twilio_wa.Client", return_value=client) as client_cls:
        sid = await send_text("15551234567", "hello")

    client_cls.assert_called_once_with(
        "ACaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "auth-token",
    )
    messages.create.assert_called_once_with(
        from_="whatsapp:+14155238886",
        to="whatsapp:+15551234567",
        body="hello",
    )
    assert sid == "SMffffffffffffffffffffffffffffffff"


@pytest.mark.asyncio
async def test_send_content_template_without_variables(monkeypatch):
    """Static Content Template uses ContentSid only — no Body or variables."""
    monkeypatch.setattr(
        "app.transport.twilio_wa.settings.twilio_account_sid",
        "ACaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    )
    monkeypatch.setattr(
        "app.transport.twilio_wa.settings.twilio_auth_token",
        "auth-token",
    )
    monkeypatch.setattr(
        "app.transport.twilio_wa.settings.twilio_whatsapp_from",
        "whatsapp:+14155238886",
    )

    created = MagicMock()
    created.sid = "SMbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    messages = MagicMock()
    messages.create.return_value = created
    client = MagicMock()
    client.messages = messages

    with patch("app.transport.twilio_wa.Client", return_value=client):
        sid = await send_content_template(
            "15551234567",
            content_sid="HXffffffffffffffffffffffffffffffff",
        )

    messages.create.assert_called_once_with(
        from_="whatsapp:+14155238886",
        to="whatsapp:+15551234567",
        content_sid="HXffffffffffffffffffffffffffffffff",
    )
    assert "body" not in messages.create.call_args.kwargs
    assert "content_variables" not in messages.create.call_args.kwargs
    assert sid == "SMbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"


@pytest.mark.asyncio
async def test_send_content_template_body_variable_maps_to_one(monkeypatch):
    monkeypatch.setattr(
        "app.transport.twilio_wa.settings.twilio_account_sid",
        "ACaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    )
    monkeypatch.setattr(
        "app.transport.twilio_wa.settings.twilio_auth_token",
        "auth-token",
    )
    monkeypatch.setattr(
        "app.transport.twilio_wa.settings.twilio_whatsapp_from",
        "whatsapp:+14155238886",
    )

    created = MagicMock()
    created.sid = "SMcccccccccccccccccccccccccccccccc"
    messages = MagicMock()
    messages.create.return_value = created
    client = MagicMock()
    client.messages = messages

    with patch("app.transport.twilio_wa.Client", return_value=client):
        await send_content_template(
            "15551234567",
            content_sid="HXaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            body_variable="hora de alongar",
        )

    kwargs = messages.create.call_args.kwargs
    assert json.loads(kwargs["content_variables"]) == {"1": "hora de alongar"}


@pytest.mark.asyncio
async def test_send_reminder_template_passes_body_variable(monkeypatch):
    monkeypatch.setattr(
        "app.transport.twilio_wa.settings.twilio_reminder_content_sid",
        "HX_REMINDER_UTILITY_SID",
    )
    with patch(
        "app.transport.twilio_wa.send_content_template", new_callable=AsyncMock
    ) as mock_send:
        mock_send.return_value = "SM123"
        from app.transport.twilio_wa import send_reminder_template

        sid = await send_reminder_template("15551234567", "Tomar remédio")
        assert sid == "SM123"
        mock_send.assert_called_once_with(
            "15551234567",
            content_sid="HX_REMINDER_UTILITY_SID",
            body_variable="Tomar remédio",
        )


@pytest.mark.asyncio
async def test_send_automation_template_passes_summary_variable(monkeypatch):
    monkeypatch.setattr(
        "app.transport.twilio_wa.settings.twilio_automation_content_sid",
        "HX_AUTOMATION_UTILITY_SID",
    )
    with patch(
        "app.transport.twilio_wa.send_content_template", new_callable=AsyncMock
    ) as mock_send:
        mock_send.return_value = "SM123"
        from app.transport.twilio_wa import send_automation_template

        sid = await send_automation_template("15551234567", "Relatório pronto")
        assert sid == "SM123"
        mock_send.assert_called_once_with(
            "15551234567",
            content_sid="HX_AUTOMATION_UTILITY_SID",
            variables={"1": "Relatório pronto"},
        )


@pytest.mark.asyncio
async def test_send_action_template_passes_summary_variable(monkeypatch):
    monkeypatch.setattr(
        "app.transport.twilio_wa.settings.twilio_action_content_sid",
        "HX_ACTION_UTILITY_SID",
    )
    with patch(
        "app.transport.twilio_wa.send_content_template", new_callable=AsyncMock
    ) as mock_send:
        mock_send.return_value = "SM123"
        from app.transport.twilio_wa import send_action_template

        sid = await send_action_template("15551234567", "Confirmar envio de email?")
        assert sid == "SM123"
        mock_send.assert_called_once_with(
            "15551234567",
            content_sid="HX_ACTION_UTILITY_SID",
            variables={"1": "Confirmar envio de email?"},
        )

