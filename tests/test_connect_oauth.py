"""Unit tests for connect-link signing, minting, and Composio connect routes."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, cast
from unittest.mock import AsyncMock, MagicMock, patch
import uuid

from fastapi.testclient import TestClient
from pydantic_ai import RunContext
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.interaction import InteractionDeps, request_interaction_integration
from app.connect.pages import landing_page, mask_phone
from app.connect.token import (
    CONNECT_MAX_AGE_SECONDS,
    CONSENT_MAX_AGE_SECONDS,
    sign_connect_token,
    unsign_connect_token,
)
from app.integrations.providers import get_provider
from app.api.main import app


def test_sign_unsign_round_trip() -> None:
    nonce = "nonce-abc-123"
    token = sign_connect_token(nonce)
    assert token != nonce
    assert unsign_connect_token(token) == nonce


def test_tampered_token_rejected() -> None:
    token = sign_connect_token("good-nonce")
    assert unsign_connect_token(token + "x") is None
    assert unsign_connect_token("not-a-token") is None


def test_expired_token_rejected() -> None:
    token = sign_connect_token("stale-nonce")
    # max_age=-1: any non-negative age is expired (itsdangerous: age > max_age).
    assert unsign_connect_token(token, max_age=-1) is None
    assert CONNECT_MAX_AGE_SECONDS == 600


def test_consent_window_outlives_the_link_window() -> None:
    # The callback fires after the provider consent screen, so it verifies the
    # state token against a longer budget than the WhatsApp link itself.
    assert CONSENT_MAX_AGE_SECONDS > CONNECT_MAX_AGE_SECONDS
    token = sign_connect_token("nonce-consent")
    assert (
        unsign_connect_token(token, max_age=CONSENT_MAX_AGE_SECONDS)
        == "nonce-consent"
    )


def test_mask_phone() -> None:
    assert mask_phone("5511999887766") == "+55••••7766"
    assert mask_phone("+55 11 99988-7766") == "+55••••7766"
    assert mask_phone("1234") == "••••"


def test_mask_phone_short_number_keeps_almost_everything_hidden() -> None:
    # Short inputs have no country code to show, and slicing both ends would
    # have exposed 6 of 7 digits behind a bogus "+".
    assert mask_phone("5551234") == "••••34"
    assert mask_phone("5551234567") == "••••67"


def test_landing_page_uses_registry_copy() -> None:
    notion = get_provider("notion")
    html = landing_page(
        masked_phone="+55••••7766",
        start_url="https://example.test/connect/notion/start?t=x",
        title=notion.landing_title_pt,
        body_pt=notion.landing_body_pt,
        cta=notion.connect_cta_pt,
    )
    assert "Conectar Notion" in html
    assert "+55••••7766" in html
    assert notion.connect_cta_pt in html


async def test_request_integration_mints_link_for_deps_contact() -> None:
    session = AsyncMock()
    session_cm = AsyncMock()
    session_cm.__aenter__.return_value = session
    session_cm.__aexit__.return_value = None
    factory = MagicMock(return_value=session_cm)
    deps = InteractionDeps(
        contact_id=99,
        phone="5511999999999",
        session_factory=cast(async_sessionmaker[AsyncSession], factory),
        tz="America/Sao_Paulo",
        interaction_run_id=uuid.uuid4(),
        event_kind="user_inbound",
    )
    ctx = cast(RunContext[InteractionDeps], MagicMock(deps=deps))

    with patch(
        "app.integrations.connect_link.create_connect_link", new_callable=AsyncMock
    ) as mint:
        result = await request_interaction_integration(ctx, "Gmail")

    mint.assert_awaited_once()
    assert mint.await_args.kwargs["contact_id"] == 99
    assert mint.await_args.kwargs["provider"] == "gmail"
    assert "/connect/gmail?t=" in result
    session.commit.assert_awaited()


async def test_request_integration_rejects_unknown_provider() -> None:
    deps = InteractionDeps(
        contact_id=1,
        phone="5511999999999",
        session_factory=cast(async_sessionmaker[AsyncSession], MagicMock()),
        tz="America/Sao_Paulo",
        interaction_run_id=uuid.uuid4(),
        event_kind="user_inbound",
    )
    ctx = cast(RunContext[InteractionDeps], MagicMock(deps=deps))
    result = await request_interaction_integration(ctx, "slack")
    assert "not supported" in result.lower()


def _session_patch(session: Any):
    @asynccontextmanager
    async def _fake() -> AsyncIterator[Any]:
        yield session

    return patch("app.api.main.get_session", _fake)


def _link(
    *,
    contact_id: int = 42,
    provider: str = "notion",
    nonce: str = "nonce-1",
) -> MagicMock:
    link = MagicMock()
    link.contact_id = contact_id
    link.provider = provider
    link.nonce = nonce
    return link


def test_connect_landing_rejects_provider_mismatch() -> None:
    token = sign_connect_token("nonce-mismatch")
    link = _link(provider="gmail", nonce="nonce-mismatch")
    session = AsyncMock()
    session.get.return_value = MagicMock(phone="5511999887766")

    with (
        _session_patch(session),
        patch("app.api.main.get_usable_connect_link", AsyncMock(return_value=link)),
    ):
        client = TestClient(app)
        response = client.get(f"/connect/notion?t={token}")

    assert response.status_code == 400
    assert "Não deu pra conectar" in response.text


def test_connect_landing_rejects_unknown_provider() -> None:
    client = TestClient(app)
    response = client.get("/connect/slack?t=whatever")
    assert response.status_code == 400
    assert "Não deu pra conectar" in response.text


def test_connect_success_upserts_external_account_id() -> None:
    token = sign_connect_token("nonce-ok")
    link = _link(provider="notion", nonce="nonce-ok")
    session = AsyncMock()
    session.get.return_value = MagicMock(phone="5511999887766")
    upsert = AsyncMock()
    notify = AsyncMock()

    with (
        _session_patch(session),
        patch("app.api.main.get_usable_connect_link", AsyncMock(return_value=link)),
        patch("app.api.main.claim_connect_link", AsyncMock(return_value=link)),
        patch(
            "app.api.main.composio_mod.find_active_connected_account_id",
            return_value="ca_notion_1",
        ),
        patch("app.api.main.upsert_integration", upsert),
        patch("app.api.main.enqueue_integration_notify", notify),
    ):
        client = TestClient(app)
        response = client.get(
            f"/connect/notion/success?t={token}",
            follow_redirects=False,
        )

    assert response.status_code == 303
    assert response.headers["location"].endswith("/connect/notion/success")
    upsert.assert_awaited_once()
    assert upsert.await_args.kwargs["provider"] == "notion"
    assert upsert.await_args.kwargs["external_account_id"] == "ca_notion_1"
    assert upsert.await_args.kwargs["contact_id"] == 42
    notify.assert_awaited_once()
    assert notify.await_args.kwargs["payload"]["provider"] == "notion"


def test_connect_success_rejects_tampered_token_without_upsert() -> None:
    upsert = AsyncMock()
    notify = AsyncMock()

    with (
        patch("app.api.main.upsert_integration", upsert),
        patch("app.api.main.enqueue_integration_notify", notify),
        patch("app.api.main.get_session") as get_session,
    ):
        client = TestClient(app)
        response = client.get("/connect/notion/success?t=not-a-real-token")

    assert response.status_code == 400
    assert "Não deu pra conectar" in response.text
    upsert.assert_not_awaited()
    notify.assert_not_awaited()
    get_session.assert_not_called()


def test_connect_success_static_page_is_reload_safe() -> None:
    client = TestClient(app)
    response = client.get("/connect/notion/success")
    assert response.status_code == 200
    assert "Conectado" in response.text
    assert "Notion" in response.text
