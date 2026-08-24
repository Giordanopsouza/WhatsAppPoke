"""Allowlisted Composio toolkit providers (MVP registry).

Unknown slugs are rejected. ``user_id`` for Composio sessions is always
``str(contact_id)`` — never a shared default (see ADR 0008).
"""

from __future__ import annotations

from dataclasses import dataclass


class UnknownProvider(ValueError):
    """Raised when a provider slug is not in the MVP allowlist."""


@dataclass(frozen=True, slots=True)
class Provider:
    """Metadata for one Composio toolkit the product can connect."""

    slug: str
    display_name: str
    landing_title_pt: str
    landing_body_pt: str
    notify_body_pt: str
    connect_cta_pt: str


# Toolkit slugs match Composio managed-auth toolkit ids. No ``googlesuper``
# (managed auth can hit Google "App is blocked" on broad scopes).
_PROVIDERS: tuple[Provider, ...] = (
    Provider(
        slug="gmail",
        display_name="Gmail",
        landing_title_pt="Conectar Gmail",
        landing_body_pt=(
            "Vamos conectar o Gmail ao WhatsApp "
            "({phone}). Só use o link que você recebeu no chat."
        ),
        notify_body_pt="Gmail conectado. Já pode pedir pra ler ou redigir e-mails.",
        connect_cta_pt="Conectar Gmail",
    ),
    Provider(
        slug="googlecalendar",
        display_name="Google Calendar",
        landing_title_pt="Conectar Google Agenda",
        landing_body_pt=(
            "Vamos conectar o Google Agenda ao WhatsApp "
            "({phone}). Só use o link que você recebeu no chat."
        ),
        notify_body_pt=(
            "Google Agenda conectada. Já pode pedir a agenda ou criar eventos."
        ),
        connect_cta_pt="Conectar Google Agenda",
    ),
    Provider(
        slug="googledrive",
        display_name="Google Drive",
        landing_title_pt="Conectar Google Drive",
        landing_body_pt=(
            "Vamos conectar o Google Drive ao WhatsApp "
            "({phone}). Só use o link que você recebeu no chat."
        ),
        notify_body_pt="Google Drive conectado. Já pode pedir pra buscar arquivos.",
        connect_cta_pt="Conectar Google Drive",
    ),
    Provider(
        slug="googlesheets",
        display_name="Google Sheets",
        landing_title_pt="Conectar Planilhas Google",
        landing_body_pt=(
            "Vamos conectar o Planilhas Google ao WhatsApp "
            "({phone}). Só use o link que você recebeu no chat."
        ),
        notify_body_pt=(
            "Planilhas Google conectadas. Já pode pedir pra ler ou atualizar."
        ),
        connect_cta_pt="Conectar Planilhas Google",
    ),
    Provider(
        slug="notion",
        display_name="Notion",
        landing_title_pt="Conectar Notion",
        landing_body_pt=(
            "Vamos conectar o Notion ao WhatsApp "
            "({phone}). Só use o link que você recebeu no chat."
        ),
        notify_body_pt="Notion conectado. Já pode pedir pra buscar ou criar páginas.",
        connect_cta_pt="Conectar Notion",
    ),
    Provider(
        slug="trello",
        display_name="Trello",
        landing_title_pt="Conectar Trello",
        landing_body_pt=(
            "Vamos conectar o Trello ao WhatsApp "
            "({phone}). Só use o link que você recebeu no chat."
        ),
        notify_body_pt="Trello conectado. Já pode pedir pra ver boards e cards.",
        connect_cta_pt="Conectar Trello",
    ),
    Provider(
        slug="clickup",
        display_name="ClickUp",
        landing_title_pt="Conectar ClickUp",
        landing_body_pt=(
            "Vamos conectar o ClickUp ao WhatsApp "
            "({phone}). Só use o link que você recebeu no chat."
        ),
        notify_body_pt="ClickUp conectado. Já pode pedir pra ver tarefas e listas.",
        connect_cta_pt="Conectar ClickUp",
    ),
)

PROVIDERS: dict[str, Provider] = {p.slug: p for p in _PROVIDERS}


# Return provider metadata or raise if the slug isn't in our allowlist.
def get_provider(slug: str) -> Provider:
    """Return provider metadata or raise ``UnknownProvider``."""
    normalized = slug.strip().lower()
    try:
        return PROVIDERS[normalized]
    except KeyError as exc:
        raise UnknownProvider(
            f'Provider "{slug}" is not supported. '
            f"Allowed: {', '.join(sorted(PROVIDERS))}."
        ) from exc


# True if this provider slug is one we support (Gmail, Calendar, etc.).
def is_known_provider(slug: str) -> bool:
    return slug.strip().lower() in PROVIDERS
