"""Owned, typed Gmail tools exposed only to Gmail-scoped Execution runs."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from pydantic_ai import RunContext

from app.core.logutil import get_logger
from app.database.models import PendingActionKind
from app.db import create_pending_action, list_active_integrations
from app.integrations.composio_proxy import AuthenticatedProxyAdapter, ProxyRequest
from app.integrations.gmail import (
    GmailPayloadError,
    MAX_SEARCH_RESULTS,
    draft_request,
    message_request,
    normalize_draft,
    normalize_message,
    normalize_metadata,
    normalize_search_page,
    search_request,
    validate_email_address,
)


_proxy = AuthenticatedProxyAdapter(toolkit="gmail")
log = get_logger(__name__)
_UNAVAILABLE = "Gmail não está conectado ou disponível para esta pessoa."
_FAILED = "Não consegui acessar o Gmail agora; tente novamente em instantes."
_EMAIL_CONTEXT = ("email", "e-mail", "e mail", "gmail", "mail")
_SEND_INTENT = ("enviar", "envie", "mandar", "mande", "disparar", "send")
_DRAFT_ONLY = ("rascunho", "draft")


def goal_implies_email_send(goal: str) -> bool:
    """True when an execution goal is to send email, not merely draft it."""
    text = " ".join(goal.lower().split())
    if not any(marker in text for marker in _EMAIL_CONTEXT):
        return False
    send_intent = any(marker in text for marker in _SEND_INTENT)
    draft_only = any(marker in text for marker in _DRAFT_ONLY) and not send_intent
    return send_intent and not draft_only


# Check if this contact has Gmail connected.
async def _integration(ctx: RunContext[Any]) -> Any | None:
    async with ctx.deps.session_factory() as session:
        rows = await list_active_integrations(session, contact_id=ctx.deps.contact_id)
    return next((row for row in rows if row.provider == "gmail"), None)


# Call Composio's authenticated proxy for a Gmail API request.
async def _execute(
    ctx: RunContext[Any], *, tool_name: str, request: ProxyRequest, integration: Any
):
    return await asyncio.to_thread(
        _proxy.execute,
        contact_id=ctx.deps.contact_id,
        integration=integration,
        owned_tool_name=tool_name,
        request=request,
    )


async def search_emails(
    ctx: RunContext[Any],
    query: str = "",
    after: str | None = None,
    before: str | None = None,
    page_token: str | None = None,
    max_results: int = 5,
) -> str:
    """Search Gmail and return bounded metadata only, never message bodies.

    after/before use YYYY-MM-DD. max_results is 1..10. Use page_token from a
    prior result for the next page; use get_email with a returned message_id.
    """
    try:
        request = search_request(
            query=query, after=after, before=before, page_token=page_token,
            max_results=max_results,
        )
    except GmailPayloadError as exc:
        return f"Busca inválida: {exc}."
    integration = await _integration(ctx)
    if integration is None:
        return _UNAVAILABLE
    try:
        page = await _execute(ctx, tool_name="search_emails", request=request, integration=integration)
        refs, next_token = normalize_search_page(page.data)
        async def fetch_metadata(ref: tuple[str, str]) -> dict[str, str] | None:
            message_id, thread_id = ref
            response = await _execute(
                ctx,
                tool_name="search_emails",
                request=message_request(message_id, metadata_only=True),
                integration=integration,
            )
            item = normalize_metadata(response.data)
            if item is not None:
                item["thread_id"] = item["thread_id"] or thread_id
            return item

        fetched = await asyncio.gather(
            *(fetch_metadata(ref) for ref in refs[:MAX_SEARCH_RESULTS])
        )
        results = [item for item in fetched if item is not None]
        return json.dumps({"emails": results, "next_page_token": next_token}, ensure_ascii=False)
    except Exception:
        log.exception(
            "gmail_search_failed",
            extra={"event": "gmail_search_failed", "contact_id": ctx.deps.contact_id},
        )
        return _FAILED


async def get_email(ctx: RunContext[Any], message_id: str) -> str:
    """Fetch one Gmail message with normalized metadata and bounded plain text."""
    integration = await _integration(ctx)
    if integration is None:
        return _UNAVAILABLE
    try:
        response = await _execute(
            ctx,
            tool_name="get_email",
            request=message_request(message_id, metadata_only=False),
            integration=integration,
        )
        message = normalize_message(response.data)
        if message is None:
            return "E-mail não encontrado. Use um message_id retornado pela busca."
        return json.dumps(message, ensure_ascii=False)
    except GmailPayloadError as exc:
        return f"message_id inválido: {exc}."
    except Exception:
        log.exception(
            "gmail_get_email_failed",
            extra={"event": "gmail_get_email_failed", "contact_id": ctx.deps.contact_id},
        )
        return _FAILED


async def create_email_draft(
    ctx: RunContext[Any],
    to: str,
    subject: str,
    body: str,
    thread_id: str | None = None,
) -> str:
    """Create a Gmail draft. This never sends it.

    After creation, use stage_send_email with the exact returned draft_id and
    summary only if the user wants it sent; sending requires a later inbound.
    """
    try:
        request = draft_request(to=to, subject=subject, body=body, thread_id=thread_id)
    except GmailPayloadError as exc:
        return f"Rascunho inválido: {exc}."
    integration = await _integration(ctx)
    if integration is None:
        return _UNAVAILABLE
    try:
        response = await _execute(
            ctx, tool_name="create_email_draft", request=request, integration=integration
        )
        draft_id, provider_thread_id = normalize_draft(response.data)
        draft_result = json.dumps(
            {
                "draft_id": draft_id,
                "thread_id": provider_thread_id or thread_id,
                "to": to.strip(),
                "subject": subject.strip(),
                "status": "draft_created_not_sent",
            },
            ensure_ascii=False,
        )
        goal = getattr(ctx.deps, "goal", "") or ""
        if isinstance(goal, str) and goal_implies_email_send(goal):
            return await stage_send_email(
                ctx, draft_id=draft_id, to=to, subject=subject
            )
        return draft_result
    except Exception:
        log.exception(
            "gmail_create_draft_failed",
            extra={"event": "gmail_create_draft_failed", "contact_id": ctx.deps.contact_id},
        )
        return _FAILED


async def stage_send_email(
    ctx: RunContext[Any], draft_id: str, to: str, subject: str
) -> str:
    """Stage an exact Gmail draft for later explicit WhatsApp confirmation.

    This function never sends. A request to draft and send in this same user
    turn still stops here and asks for a later confirmation message.
    """
    clean_draft = draft_id.strip()
    clean_subject = subject.strip()
    try:
        recipient = validate_email_address(to)
        if not clean_draft or len(clean_draft) > 256:
            raise GmailPayloadError("invalid Gmail draft id")
        if not clean_subject or len(clean_subject) > 300:
            raise GmailPayloadError("invalid email subject")
    except GmailPayloadError as exc:
        return f"Envio inválido: {exc}."
    if await _integration(ctx) is None:
        return _UNAVAILABLE

    payload = {"draft_id": clean_draft, "to": recipient, "subject": clean_subject}
    execution_run_id = getattr(ctx.deps, "execution_run_id", None)
    async with ctx.deps.session_factory() as session:
        row = await create_pending_action(
            session,
            contact_id=ctx.deps.contact_id,
            kind=PendingActionKind.SEND_EMAIL,
            payload=payload,
            turn_id=str(execution_run_id),
            source_interaction_run_id=getattr(
                ctx.deps, "source_interaction_run_id", None
            ),
            source_execution_run_id=execution_run_id,
        )
        await session.commit()
    return json.dumps(
        {
            "status": "awaiting_later_confirmation",
            "action_id": str(row.id),
            "draft_id": clean_draft,
            "to": recipient,
            "subject": clean_subject,
            "expires_in_minutes": 15,
        },
        ensure_ascii=False,
    )
