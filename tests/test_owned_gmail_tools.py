"""Task 043 contracts for the owned Gmail surface and send confirmation."""

from __future__ import annotations

import json
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.agent import gmail_tools
from app.agent.interaction import INTERACTION_INSTRUCTIONS, confirm_email_send
from app.database.models import PendingActionKind
from app.integrations.composio_proxy import ProxyResponse, ProxyUnavailable
from app.integrations.gmail import (
    MAX_BODY_CHARS,
    MAX_SEARCH_RESULTS,
    draft_request,
    normalize_message,
    search_request,
)
from app.services import email_confirmation
from app.services.email_confirmation import _select_email_action, confirm_staged_email


FIXTURES = Path(__file__).parent / "fixtures"


def _fixture(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@asynccontextmanager
async def _session_context(session: AsyncMock):
    yield session


def _session_factory(session: AsyncMock) -> MagicMock:
    return MagicMock(side_effect=lambda: _session_context(session))


def _pending(*, kind: PendingActionKind = PendingActionKind.SEND_EMAIL):
    payload = {"draft_id": "draft-1", "to": "ana@example.com", "subject": "Oi"}
    return SimpleNamespace(
        id=uuid.uuid4(),
        kind=kind,
        payload=payload,
        payload_hash=email_confirmation.payload_hash(payload),
    )


def test_search_request_bounds_query_dates_page_and_count() -> None:
    request = search_request(
        query="from:ana@example.com",
        after="2026-08-01",
        before="2026-08-16",
        page_token="next-1",
        max_results=MAX_SEARCH_RESULTS,
    )
    params = {(item.name, item.value) for item in request.parameters}
    assert request.endpoint == "/gmail/v1/users/me/messages"
    assert ("maxResults", "10") in params
    assert (
        "q",
        "from:ana@example.com after:2026/08/01 before:2026/08/16",
    ) in params
    assert ("pageToken", "next-1") in params


def test_html_and_multipart_fetches_are_plain_and_bounded() -> None:
    html_message = normalize_message(_fixture("gmail_message_html.json"))
    multipart = normalize_message(_fixture("gmail_message_multipart.json"))
    assert html_message is not None
    assert "<html" not in html_message["body"]
    assert "Segue o resumo" in html_message["body"]
    assert multipart is not None
    assert multipart["body"] == "Versao texto puro do multipart."
    assert len(html_message["body"]) <= MAX_BODY_CHARS


def test_draft_payload_is_owned_mime_and_never_a_send() -> None:
    request = draft_request(
        to="ana@example.com",
        subject="Resumo",
        body="Segue o resumo.",
        thread_id="thread-1",
    )
    assert request.endpoint == "/gmail/v1/users/me/drafts"
    assert request.method == "POST"
    assert request.body is not None
    assert set(request.body) == {"message"}
    assert "raw" in request.body["message"]
    assert request.body["message"]["threadId"] == "thread-1"


async def test_disconnected_gmail_does_not_call_proxy() -> None:
    ctx = SimpleNamespace(deps=SimpleNamespace(contact_id=7, session_factory=MagicMock()))
    with (
        patch.object(gmail_tools, "_integration", AsyncMock(return_value=None)),
        patch.object(gmail_tools, "_execute", AsyncMock()) as execute,
    ):
        result = await gmail_tools.search_emails(ctx, query="is:unread")
    assert "não está conectado" in result
    execute.assert_not_awaited()


async def test_search_normalizes_metadata_without_fetching_full_body() -> None:
    ctx = SimpleNamespace(deps=SimpleNamespace(contact_id=7, session_factory=MagicMock()))
    integration = SimpleNamespace(provider="gmail")
    execute = AsyncMock(
        side_effect=[
            ProxyResponse(200, _fixture("gmail_messages_list.json"), {}),
            ProxyResponse(200, _fixture("gmail_message_plain.json"), {}),
            ProxyResponse(200, _fixture("gmail_message_html.json"), {}),
        ]
    )
    with (
        patch.object(gmail_tools, "_integration", AsyncMock(return_value=integration)),
        patch.object(gmail_tools, "_execute", execute),
    ):
        result = json.loads(await gmail_tools.search_emails(ctx, query="is:unread"))
    assert len(result["emails"]) == 2
    assert all("body" not in item for item in result["emails"])
    metadata_requests = [call.kwargs["request"] for call in execute.await_args_list[1:]]
    assert all(
        any(p.name == "format" and p.value == "metadata" for p in request.parameters)
        for request in metadata_requests
    )


async def test_gmail_failures_are_logged_without_returning_error_details() -> None:
    ctx = SimpleNamespace(deps=SimpleNamespace(contact_id=7, session_factory=MagicMock()))
    with (
        patch.object(gmail_tools, "_integration", AsyncMock(return_value=object())),
        patch.object(gmail_tools, "_execute", AsyncMock(side_effect=RuntimeError("provider failed"))),
        patch.object(gmail_tools.log, "exception") as log_exception,
    ):
        result = await gmail_tools.get_email(ctx, message_id="message-1")
    assert result == "Não consegui acessar o Gmail agora; tente novamente em instantes."
    log_exception.assert_called_once_with(
        "gmail_get_email_failed",
        extra={"event": "gmail_get_email_failed", "contact_id": 7},
    )


async def test_stage_send_stores_exact_summary_and_source_execution() -> None:
    session = AsyncMock()
    execution_id = uuid.uuid4()
    interaction_id = uuid.uuid4()
    ctx = SimpleNamespace(
        deps=SimpleNamespace(
            contact_id=7,
            execution_run_id=execution_id,
            source_interaction_run_id=interaction_id,
            session_factory=_session_factory(session),
        )
    )
    staged = SimpleNamespace(id=uuid.uuid4())
    with (
        patch.object(gmail_tools, "_integration", AsyncMock(return_value=object())),
        patch.object(gmail_tools, "create_pending_action", AsyncMock(return_value=staged)) as create,
    ):
        result = json.loads(
            await gmail_tools.stage_send_email(
                ctx, draft_id="draft-1", to="ana@example.com", subject="Resumo"
            )
        )
    assert result["status"] == "awaiting_later_confirmation"
    assert create.await_args.kwargs["payload"] == {
        "draft_id": "draft-1",
        "to": "ana@example.com",
        "subject": "Resumo",
    }
    assert create.await_args.kwargs["source_execution_run_id"] == execution_id
    assert create.await_args.kwargs["source_interaction_run_id"] == interaction_id
    assert create.await_args.kwargs["turn_id"] == str(execution_id)


def test_goal_implies_email_send() -> None:
    assert gmail_tools.goal_implies_email_send(
        "Enviar um email para ana@example.com avisando sobre o evento"
    )
    assert not gmail_tools.goal_implies_email_send(
        "Preparar rascunho do e-mail para ana@example.com"
    )
    assert not gmail_tools.goal_implies_email_send("buscar emails não lidos")


async def test_create_email_draft_auto_stages_when_goal_implies_send() -> None:
    execution_id = uuid.uuid4()
    ctx = SimpleNamespace(
        deps=SimpleNamespace(
            contact_id=7,
            goal="Enviar um email para ana@example.com",
            execution_run_id=execution_id,
            source_interaction_run_id=uuid.uuid4(),
            session_factory=MagicMock(),
        )
    )
    staged = json.dumps({"status": "awaiting_later_confirmation", "action_id": "1"})
    with patch.object(
        gmail_tools,
        "stage_send_email",
        AsyncMock(return_value=staged),
    ) as stage:
        with (
            patch.object(gmail_tools, "_integration", AsyncMock(return_value=object())),
            patch.object(
                gmail_tools,
                "_execute",
                AsyncMock(
                    return_value=SimpleNamespace(
                        data={"id": "draft-1", "message": {"threadId": "thread-1"}}
                    )
                ),
            ),
            patch.object(gmail_tools, "normalize_draft", return_value=("draft-1", "thread-1")),
        ):
            result = await gmail_tools.create_email_draft(
                ctx, to="ana@example.com", subject="Oi", body="Corpo"
            )
    assert result == staged
    stage.assert_awaited_once_with(
        ctx, draft_id="draft-1", to="ana@example.com", subject="Oi"
    )


async def test_create_email_draft_stays_draft_only_without_send_goal() -> None:
    ctx = SimpleNamespace(
        deps=SimpleNamespace(
            contact_id=7,
            goal="Preparar rascunho do e-mail para ana@example.com",
            session_factory=MagicMock(),
        )
    )
    with (
        patch.object(gmail_tools, "_integration", AsyncMock(return_value=object())),
        patch.object(
            gmail_tools,
            "_execute",
            AsyncMock(
                return_value=SimpleNamespace(
                    data={"id": "draft-1", "message": {"threadId": "thread-1"}}
                )
            ),
        ),
        patch.object(gmail_tools, "normalize_draft", return_value=("draft-1", "thread-1")),
        patch.object(gmail_tools, "stage_send_email", AsyncMock()) as stage,
    ):
        result = json.loads(
            await gmail_tools.create_email_draft(
                ctx, to="ana@example.com", subject="Oi", body="Corpo"
            )
        )
    assert result["status"] == "draft_created_not_sent"
    stage.assert_not_awaited()


async def test_interaction_interprets_confirmation_but_internal_reentry_cannot_send() -> None:
    assert '"sim"' in INTERACTION_INSTRUCTIONS
    assert '"pode enviar"' in INTERACTION_INSTRUCTIONS
    ctx = SimpleNamespace(
        deps=SimpleNamespace(
            event_kind="execution_result",
            inbound_turn_id="",
            contact_id=7,
            session_factory=MagicMock(),
        )
    )
    with patch("app.agent.interaction.confirm_staged_email", AsyncMock()) as confirm:
        result = await confirm_email_send(ctx)
    assert "requires a new user message" in result
    confirm.assert_not_awaited()


async def test_confirmation_rejects_wrong_contact_expired_or_duplicate() -> None:
    session = AsyncMock()
    factory = _session_factory(session)
    with patch.object(email_confirmation, "list_pending_actions", AsyncMock(return_value=[])):
        outcome = await confirm_staged_email(
            contact_id=999,
            inbound_turn_id="SM-later",
            session_factory=factory,
        )
    assert outcome.state == "none"


async def test_same_turn_claim_cannot_send() -> None:
    session = AsyncMock()
    pending = _pending()
    with (
        patch.object(email_confirmation, "list_pending_actions", AsyncMock(return_value=[pending])),
        patch.object(email_confirmation, "claim_pending_action", AsyncMock(return_value=None)) as claim,
    ):
        outcome = await confirm_staged_email(
            contact_id=7,
            inbound_turn_id="same-turn",
            session_factory=_session_factory(session),
        )
    assert outcome.state == "none"
    assert claim.await_args.kwargs["turn_id"] == "same-turn"


def test_email_confirmation_selects_one_email_among_other_action_kinds() -> None:
    email = _pending()
    assert _select_email_action(
        [email, _pending(kind=PendingActionKind.CREATE_EVENT)], action_id=None
    ) == email


def test_email_confirmation_requires_action_id_for_multiple_email_actions() -> None:
    assert _select_email_action([_pending(), _pending()], action_id=None) is None


async def test_successful_later_send_is_terminal_and_uses_fixed_draft() -> None:
    session = AsyncMock()
    pending = _pending()
    integration = SimpleNamespace(provider="gmail")
    proxy = MagicMock()
    proxy.execute.return_value = ProxyResponse(200, {"id": "msg-sent"}, {})
    discard = AsyncMock()
    with (
        patch.object(email_confirmation, "list_pending_actions", AsyncMock(return_value=[pending])),
        patch.object(email_confirmation, "claim_pending_action", AsyncMock(return_value=pending)),
        patch.object(email_confirmation, "list_active_integrations", AsyncMock(return_value=[integration])),
        patch.object(email_confirmation, "discard_pending_action", discard),
    ):
        outcome = await confirm_staged_email(
            contact_id=7,
            inbound_turn_id="SM-later",
            session_factory=_session_factory(session),
            proxy=proxy,
        )
    assert outcome.state == "sent"
    request = proxy.execute.call_args.kwargs["request"]
    assert request.endpoint == "/gmail/v1/users/me/drafts/send"
    assert request.body == {"id": "draft-1"}
    discard.assert_awaited_once_with(session, action_id=pending.id)


async def test_send_failure_releases_for_later_retry() -> None:
    session = AsyncMock()
    pending = _pending()
    proxy = MagicMock()
    proxy.execute.side_effect = ProxyUnavailable("down")
    release = AsyncMock()
    with (
        patch.object(email_confirmation, "list_pending_actions", AsyncMock(return_value=[pending])),
        patch.object(email_confirmation, "claim_pending_action", AsyncMock(return_value=pending)),
        patch.object(
            email_confirmation,
            "list_active_integrations",
            AsyncMock(return_value=[SimpleNamespace(provider="gmail")]),
        ),
        patch.object(email_confirmation, "release_pending_action", release),
    ):
        outcome = await confirm_staged_email(
            contact_id=7,
            inbound_turn_id="SM-later",
            session_factory=_session_factory(session),
            proxy=proxy,
        )
    assert outcome.state == "retryable_failure"
    release.assert_awaited_once_with(session, action_id=pending.id)
