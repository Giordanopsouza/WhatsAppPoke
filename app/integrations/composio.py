"""Thin Composio v3 SDK wrapper for managed auth and account verification."""

from __future__ import annotations

from typing import Any

from composio import Composio

from app.core.config import settings


# Create a Composio SDK client using our API key from settings.
def get_client(*, api_key: str | None = None) -> Composio:
    """Return a Composio SDK client (api key from settings by default)."""
    return Composio(api_key=api_key or settings.composio_api_key)


# Open a Composio session scoped to one WhatsApp contact (user_id = contact_id).
def create_session(
    contact_id: int,
    *,
    toolkits: list[str] | None = None,
    manage_connections: bool = False,
    client: Composio | None = None,
) -> Any:
    """Create a per-contact Composio session.

    ``user_id`` is always ``str(contact_id)`` — never a shared default.
    """
    composio = client or get_client()
    kwargs: dict[str, Any] = {
        "user_id": str(contact_id),
        "manage_connections": manage_connections,
    }
    if toolkits is not None:
        kwargs["toolkits"] = toolkits
    return composio.create(**kwargs)


# Start OAuth for Gmail/Calendar/etc. and get the browser redirect URL.
def authorize(
    contact_id: int,
    toolkit: str,
    *,
    callback_url: str,
    client: Composio | None = None,
) -> Any:
    """Start managed-auth for ``toolkit``; returns a connection request.

    The returned object exposes ``redirect_url`` for the browser 302.
    """
    session = create_session(
        contact_id,
        toolkits=[toolkit],
        manage_connections=True,
        client=client,
    )
    return session.authorize(toolkit, callback_url=callback_url)


# List connected accounts for a contact, optionally filtered by toolkit.
def list_connected_accounts(
    contact_id: int,
    *,
    toolkit: str | None = None,
    statuses: list[str] | None = None,
    client: Composio | None = None,
) -> Any:
    """List Composio connected accounts for this contact (optional toolkit)."""
    composio = client or get_client()
    kwargs: dict[str, Any] = {"user_ids": [str(contact_id)]}
    if toolkit is not None:
        kwargs["toolkit_slugs"] = [toolkit]
    if statuses is not None:
        kwargs["statuses"] = statuses
    return composio.connected_accounts.list(**kwargs)


# Find the active Composio account id (ca_…) for a contact + toolkit.
def find_active_connected_account_id(
    contact_id: int,
    toolkit: str,
    *,
    client: Composio | None = None,
) -> str | None:
    """Return the newest ACTIVE ``ca_…`` for this contact+toolkit, or None."""
    response = list_connected_accounts(
        contact_id,
        toolkit=toolkit,
        statuses=["ACTIVE"],
        client=client,
    )
    items = getattr(response, "items", None) or []
    user_id = str(contact_id)
    active: list[Any] = []
    for item in items:
        if getattr(item, "status", None) != "ACTIVE":
            continue
        if getattr(item, "is_disabled", False):
            continue
        if str(getattr(item, "user_id", "")) != user_id:
            continue
        toolkit_obj = getattr(item, "toolkit", None)
        slug = getattr(toolkit_obj, "slug", None)
        if slug != toolkit:
            continue
        account_id = getattr(item, "id", None)
        if isinstance(account_id, str) and account_id:
            active.append(item)
    if not active:
        return None
    active.sort(
        key=lambda item: getattr(item, "updated_at", None) or "",
        reverse=True,
    )
    return active[0].id


# Double-check a stored account id is still active and belongs to this contact.
def is_active_connected_account(
    contact_id: int,
    toolkit: str,
    external_account_id: str,
    *,
    client: Composio | None = None,
) -> bool:
    """Verify an exact account is active and belongs to this contact/toolkit.

    The authenticated-proxy API is selected by a ``ca_…`` id, so that id must
    never be trusted merely because it was persisted locally.  Keep this check
    here, beside the Connect-flow account lookup, so all Composio account
    tenancy rules use ``user_id=str(contact_id)``.
    """
    response = list_connected_accounts(
        contact_id,
        toolkit=toolkit,
        statuses=["ACTIVE"],
        client=client,
    )
    user_id = str(contact_id)
    for item in getattr(response, "items", None) or []:
        if getattr(item, "id", None) != external_account_id:
            continue
        if getattr(item, "status", None) != "ACTIVE":
            continue
        if getattr(item, "is_disabled", False):
            continue
        if str(getattr(item, "user_id", "")) != user_id:
            continue
        toolkit_obj = getattr(item, "toolkit", None)
        if getattr(toolkit_obj, "slug", None) != toolkit:
            continue
        return True
    return False


# Remove a Composio connected account (and optionally revoke tokens).
def delete_connected_account(
    external_account_id: str,
    *,
    revoke_on_delete: bool = True,
    client: Composio | None = None,
) -> Any:
    """Delete a Composio connected account (``ca_…``). Used by 021 purge."""
    composio = client or get_client()
    return composio.connected_accounts.delete(
        external_account_id,
        revoke_on_delete=revoke_on_delete,
    )
