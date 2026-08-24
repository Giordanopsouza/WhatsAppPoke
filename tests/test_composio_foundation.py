"""Unit tests for Composio foundation (registry + client import)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.core.config import settings
from app.integrations import composio as composio_mod
from app.integrations.providers import (
    PROVIDERS,
    UnknownProvider,
    get_provider,
    is_known_provider,
)


def test_registry_lists_mvp_toolkits() -> None:
    assert set(PROVIDERS) == {
        "gmail",
        "googlecalendar",
        "googledrive",
        "googlesheets",
        "notion",
        "trello",
        "clickup",
    }
    assert "googlesuper" not in PROVIDERS
    assert "google" not in PROVIDERS


def test_registry_rejects_unknown_provider() -> None:
    with pytest.raises(UnknownProvider, match="not supported"):
        get_provider("slack")
    assert not is_known_provider("slack")
    assert is_known_provider("Gmail")


def test_registry_provider_has_pt_copy() -> None:
    gmail = get_provider("gmail")
    assert gmail.display_name == "Gmail"
    assert "{phone}" in gmail.landing_body_pt
    assert gmail.notify_body_pt
    assert gmail.connect_cta_pt


def test_composio_client_module_importable_with_settings() -> None:
    assert settings.composio_api_key
    client = composio_mod.get_client()
    assert client is not None
    assert callable(composio_mod.create_session)
    assert callable(composio_mod.authorize)
    assert callable(composio_mod.list_connected_accounts)
    assert callable(composio_mod.find_active_connected_account_id)
    assert callable(composio_mod.is_active_connected_account)
    assert callable(composio_mod.delete_connected_account)


def test_find_active_connected_account_id_picks_newest_active() -> None:
    older = MagicMock(
        id="ca_old",
        status="ACTIVE",
        is_disabled=False,
        user_id="7",
        toolkit=MagicMock(slug="gmail"),
        updated_at="2026-01-01T00:00:00Z",
    )
    newer = MagicMock(
        id="ca_new",
        status="ACTIVE",
        is_disabled=False,
        user_id="7",
        toolkit=MagicMock(slug="gmail"),
        updated_at="2026-08-01T00:00:00Z",
    )
    other_user = MagicMock(
        id="ca_other",
        status="ACTIVE",
        is_disabled=False,
        user_id="8",
        toolkit=MagicMock(slug="gmail"),
        updated_at="2026-09-01T00:00:00Z",
    )
    response = MagicMock(items=[older, newer, other_user])
    client = MagicMock()
    client.connected_accounts.list.return_value = response

    found = composio_mod.find_active_connected_account_id(
        7,
        "gmail",
        client=client,
    )
    assert found == "ca_new"
    client.connected_accounts.list.assert_called_once()
    kwargs = client.connected_accounts.list.call_args.kwargs
    assert kwargs["user_ids"] == ["7"]
    assert kwargs["toolkit_slugs"] == ["gmail"]
    assert kwargs["statuses"] == ["ACTIVE"]


def test_active_connected_account_requires_exact_contact_toolkit_and_id() -> None:
    account = MagicMock(
        id="ca_expected",
        status="ACTIVE",
        is_disabled=False,
        user_id="7",
        toolkit=MagicMock(slug="gmail"),
    )
    client = MagicMock()
    client.connected_accounts.list.return_value = MagicMock(items=[account])

    assert composio_mod.is_active_connected_account(
        7, "gmail", "ca_expected", client=client
    )
    assert not composio_mod.is_active_connected_account(
        8, "gmail", "ca_expected", client=client
    )
    assert not composio_mod.is_active_connected_account(
        7, "googlecalendar", "ca_expected", client=client
    )
    assert not composio_mod.is_active_connected_account(
        7, "gmail", "ca_other", client=client
    )
