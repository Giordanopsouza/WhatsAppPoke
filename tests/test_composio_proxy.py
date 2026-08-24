"""Contract tests for the backend-only direct authenticated proxy."""

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.agent.owned_tools import OWNED_TOOLKITS, build_owned_toolset
from app.database.models import IntegrationStatus
from app.integrations.composio_proxy import (
    AuthenticatedProxyAdapter,
    ConnectedAccountUnavailable,
    ProviderRequestFailed,
    ProxyParameter,
    ProxyRequest,
    ProxyUnavailable,
)


def _integration(**overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "contact_id": 7,
        "provider": "gmail",
        "status": IntegrationStatus.ACTIVE,
        "external_account_id": "ca_expected",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _adapter(client: MagicMock) -> AuthenticatedProxyAdapter:
    return AuthenticatedProxyAdapter(toolkit="gmail", client_factory=lambda: client)


@pytest.mark.parametrize("provider_status", [200, 200.0])
def test_proxy_uses_verified_exact_connected_account(provider_status: int | float) -> None:
    client = MagicMock()
    client.tools.proxy.return_value = SimpleNamespace(
        status=provider_status, data={"id": "x"}, headers={}
    )
    request = ProxyRequest(
        endpoint="/gmail/v1/users/me/profile",
        method="GET",
        parameters=(ProxyParameter("maxResults", "10", "query"),),
    )

    with patch("app.integrations.composio_proxy.composio_mod.is_active_connected_account", return_value=True) as verify:
        response = _adapter(client).execute(
            contact_id=7,
            integration=_integration(),
            owned_tool_name="get_profile",
            request=request,
        )

    assert response.status == 200
    verify.assert_called_once_with(7, "gmail", "ca_expected", client=client)
    assert client.tools.proxy.call_args.kwargs["connected_account_id"] == "ca_expected"
    assert client.tools.proxy.call_args.kwargs["endpoint"] == request.endpoint
    assert client.tools.proxy.call_args.kwargs["method"] == "GET"
    assert client.tools.proxy.call_args.kwargs["parameters"] == [
        {"name": "maxResults", "type": "query", "value": "10"}
    ]


@pytest.mark.parametrize(
    "integration",
    [
        _integration(status=IntegrationStatus.REVOKED),
        _integration(external_account_id=None),
        _integration(contact_id=8),
        _integration(provider="googlecalendar"),
    ],
)
def test_proxy_rejects_local_invalid_account_before_proxy_execution(integration: SimpleNamespace) -> None:
    client = MagicMock()
    with pytest.raises(ConnectedAccountUnavailable):
        _adapter(client).execute(
            contact_id=7,
            integration=integration,
            owned_tool_name="owned",
            request=ProxyRequest(endpoint="/fixed", method="GET"),
        )
    client.tools.proxy.assert_not_called()


def test_proxy_rejects_revoked_remote_account_before_proxy_execution() -> None:
    client = MagicMock()
    with patch("app.integrations.composio_proxy.composio_mod.is_active_connected_account", return_value=False):
        with pytest.raises(ConnectedAccountUnavailable):
            _adapter(client).execute(
                contact_id=7,
                integration=_integration(),
                owned_tool_name="owned",
                request=ProxyRequest(endpoint="/fixed", method="GET"),
            )
    client.tools.proxy.assert_not_called()


@pytest.mark.parametrize("provider_status", [401, 401.0])
def test_proxy_redacts_provider_errors_and_logs_no_request_content(
    caplog: pytest.LogCaptureFixture, provider_status: int | float
) -> None:
    client = MagicMock()
    client.tools.proxy.return_value = SimpleNamespace(
        status=provider_status,
        data={"error": "token top-secret", "body": "private email"},
        headers={},
    )
    request = ProxyRequest(
        endpoint="/private?token=top-secret",
        method="POST",
        body={"message": "private email"},
    )
    with patch("app.integrations.composio_proxy.composio_mod.is_active_connected_account", return_value=True):
        with caplog.at_level(logging.INFO):
            with pytest.raises(ProviderRequestFailed, match="provider request failed") as error:
                _adapter(client).execute(
                    contact_id=7,
                    integration=_integration(),
                    owned_tool_name="search_emails",
                    request=request,
                )

    assert error.value.status == 401
    rendered = "\n".join(record.getMessage() for record in caplog.records)
    assert "top-secret" not in rendered
    assert "private email" not in rendered
    record = caplog.records[-1]
    assert record.contact_id == 7
    assert record.toolkit == "gmail"
    assert record.owned_tool == "search_emails"
    assert record.status == 401
    assert hasattr(record, "duration_ms")


def test_proxy_normalizes_sdk_exception() -> None:
    client = MagicMock()
    client.tools.proxy.side_effect = RuntimeError("raw provider token: secret")
    with patch("app.integrations.composio_proxy.composio_mod.is_active_connected_account", return_value=True):
        with pytest.raises(ProxyUnavailable, match="authenticated proxy unavailable"):
            _adapter(client).execute(
                contact_id=7,
                integration=_integration(),
                owned_tool_name="owned",
                request=ProxyRequest(endpoint="/fixed", method="GET"),
            )


def test_proxy_rejects_authorization_header() -> None:
    with pytest.raises(ValueError, match="injected by Composio"):
        ProxyParameter("Authorization", "secret", "header").as_composio()


def test_as_composio_uses_type_not_in() -> None:
    assert ProxyParameter("maxResults", "10", "query").as_composio() == {
        "name": "maxResults",
        "type": "query",
        "value": "10",
    }
    assert ProxyParameter("Accept", "application/json", "header").as_composio() == {
        "name": "Accept",
        "type": "header",
        "value": "application/json",
    }


def test_owned_registry_exposes_only_app_owned_connected_toolkits() -> None:
    assert OWNED_TOOLKITS == {"gmail", "googlecalendar"}
    assert build_owned_toolset(active_toolkits=("notion",)) is None
    gmail = build_owned_toolset(active_toolkits=("gmail",))
    assert gmail is not None
    assert set(gmail.tools) == {
        "search_emails",
        "get_email",
        "create_email_draft",
        "stage_send_email",
    }
    calendar = build_owned_toolset(active_toolkits=("googlecalendar",))
    assert calendar is not None
    assert set(calendar.tools) == {
        "list_calendars",
        "list_events",
        "get_event",
        "stage_create_event",
    }
