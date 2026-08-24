"""Backend-only adapter for Composio's direct authenticated proxy.

This is infrastructure for owned Gmail/Calendar business functions, not a
Pydantic AI tool.  Callers bind the toolkit, endpoint, method, and normalized
request in application code; the model never receives a generic HTTP surface.
"""

from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Any, Callable, Literal, Mapping

from composio import Composio
import logfire

from app.core.logutil import get_logger
from app.database.models import IntegrationStatus
from app.integrations import composio as composio_mod


log = get_logger(__name__)

ProxyMethod = Literal["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD"]
ParameterLocation = Literal["header", "query"]


class AuthenticatedProxyError(Exception):
    """Base class for non-sensitive proxy failures returned to owned tools."""


class ConnectedAccountUnavailable(AuthenticatedProxyError):
    """The local or Composio account cannot safely be used."""


class ProxyUnavailable(AuthenticatedProxyError):
    """Composio could not execute the request."""


class ProviderRequestFailed(AuthenticatedProxyError):
    """The provider returned an unsuccessful status; payload is discarded."""

    def __init__(self, status: int | None) -> None:
        self.status = status
        super().__init__("provider request failed")


@dataclass(frozen=True)
class ProxyParameter:
    """One pre-normalized fixed header or query parameter from trusted code."""

    name: str
    value: str
    location: ParameterLocation

    # Convert to the format Composio's proxy API expects.
    def as_composio(self) -> dict[str, str]:
        if self.location == "header" and self.name.casefold() == "authorization":
            raise ValueError("Authorization is injected by Composio")
        return {"name": self.name, "type": self.location, "value": self.value}


@dataclass(frozen=True)
class ProxyRequest:
    """A request assembled by an owned tool, never by model arguments."""

    endpoint: str
    method: ProxyMethod
    body: Mapping[str, Any] | None = None
    parameters: tuple[ProxyParameter, ...] = ()


@dataclass(frozen=True)
class ProxyResponse:
    """Raw proxy result for the owning business tool to normalize and bound."""

    status: int
    data: Any
    headers: Mapping[str, Any] | None


class AuthenticatedProxyAdapter:
    """Execute fixed requests using one contact-owned Composio connection."""

    # Bind this adapter to one toolkit (gmail or googlecalendar).
    def __init__(
        self,
        *,
        toolkit: str,
        client_factory: Callable[[], Composio] = composio_mod.get_client,
    ) -> None:
        self.toolkit = toolkit
        self._client_factory = client_factory

    def execute(
        self,
        *,
        contact_id: int,
        integration: Any,
        owned_tool_name: str,
        request: ProxyRequest,
    ) -> ProxyResponse:
        """Verify account tenancy, then make one direct proxy request.

        Logs intentionally omit endpoint, parameters, bodies, provider payloads,
        and credentials.  The owning tool is responsible for turning ``data``
        into a compact result before it reaches a model.
        """
        started = time.monotonic()
        status: int | None = None
        try:
            account_id = self._validated_account_id(
                contact_id=contact_id, integration=integration
            )
            client = self._client_factory()
            if not composio_mod.is_active_connected_account(
                contact_id,
                self.toolkit,
                account_id,
                client=client,
            ):
                raise ConnectedAccountUnavailable("connected account is unavailable")

            with logfire.span(
                "proxy_tool",
                contact_id=contact_id,
                toolkit=self.toolkit,
                owned_tool=owned_tool_name,
            ):
                response = client.tools.proxy(
                    endpoint=request.endpoint,
                    method=request.method,
                    body=dict(request.body) if request.body is not None else None,
                    connected_account_id=account_id,
                    parameters=[item.as_composio() for item in request.parameters] or None,
                )
            raw_status = getattr(response, "status", None)
            if isinstance(raw_status, bool):
                status = None
            elif isinstance(raw_status, int):
                status = raw_status
            elif isinstance(raw_status, float) and raw_status.is_integer():
                status = int(raw_status)
            else:
                status = None
            if status is None or not 200 <= status < 300:
                raise ProviderRequestFailed(status)
            return ProxyResponse(
                status=status,
                data=getattr(response, "data", None),
                headers=getattr(response, "headers", None),
            )
        except AuthenticatedProxyError:
            raise
        except Exception as exc:
            raise ProxyUnavailable("authenticated proxy unavailable") from exc
        finally:
            log.info(
                "composio_proxy",
                extra={
                    "event": "composio_proxy",
                    "contact_id": contact_id,
                    "toolkit": self.toolkit,
                    "owned_tool": owned_tool_name,
                    "status": status,
                    "duration_ms": round((time.monotonic() - started) * 1000),
                },
            )

    # Make sure the integration row belongs to this contact and toolkit.
    def _validated_account_id(self, *, contact_id: int, integration: Any) -> str:
        if getattr(integration, "contact_id", None) != contact_id:
            raise ConnectedAccountUnavailable("integration does not belong to contact")
        if getattr(integration, "provider", None) != self.toolkit:
            raise ConnectedAccountUnavailable("integration toolkit does not match")
        status = getattr(integration, "status", None)
        if status not in (IntegrationStatus.ACTIVE, IntegrationStatus.ACTIVE.value):
            raise ConnectedAccountUnavailable("integration is not active")
        account_id = getattr(integration, "external_account_id", None)
        if not isinstance(account_id, str) or not account_id:
            raise ConnectedAccountUnavailable("integration has no connected account")
        return account_id
