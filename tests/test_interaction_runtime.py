"""Task 040 contracts: typed output, outbound fuse/idempotency, and retries."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
import uuid

from pydantic_ai import models
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.models.test import TestModel
from fastapi import BackgroundTasks
from fastapi.testclient import TestClient

from app.agent.history import history_to_messages, history_to_prompt_and_messages
from app.agent.interaction import (
    FALLBACK_REPLY,
    INTERACTION_INSTRUCTIONS,
    INTERNAL_CONTEXT_OPEN,
    wrap_internal_context,
    InteractionDeps,
    InteractionEventKind,
    InteractionOutput,
    _send_visible,
    agent_interaction,
    dispatch_execution,
    run_interaction_event,
)
from app.services.execution import DispatchOutcome
from app.database.models import MessageDeliveryState, PendingActionKind
from app.db import session as session_mod


models.ALLOW_MODEL_REQUESTS = False


@asynccontextmanager
async def _session_context(session: AsyncMock):
    yield session


def _deps(event_kind: InteractionEventKind = "user_inbound") -> InteractionDeps:
    session = AsyncMock()
    return InteractionDeps(
        contact_id=7,
        phone="15551234567",
        tz="America/Sao_Paulo",
        interaction_run_id=uuid.uuid4(),
        session_factory=MagicMock(side_effect=lambda: _session_context(session)),
        event_kind=event_kind,
    )


def _context_result():
    return (
        SimpleNamespace(tz="America/Sao_Paulo"),
        [{"role": "user", "content": "oi"}],
        (),
        "nenhuma",
        "nenhuma",
    )


async def test_dispatch_execution_denied_on_execution_result_reentry() -> None:
    deps = _deps("execution_result")
    ctx = SimpleNamespace(deps=deps)
    with patch(
        "app.agent.interaction.dispatch_detached_execution", AsyncMock()
    ) as dispatch:
        result = await dispatch_execution(
            ctx, goal="buscar emails", toolkits=["gmail"]
        )

    dispatch.assert_not_awaited()
    assert result == (
        '{"state":"unavailable","detail":"dispatch requires a new user message"}'
    )


async def test_dispatch_execution_allowed_on_user_inbound() -> None:
    deps = _deps("user_inbound")
    ctx = SimpleNamespace(deps=deps)
    execution_id = uuid.uuid4()
    with patch(
        "app.agent.interaction.dispatch_detached_execution",
        AsyncMock(
            return_value=DispatchOutcome("started", execution_run_id=execution_id)
        ),
    ) as dispatch:
        result = await dispatch_execution(
            ctx, goal="buscar emails", toolkits=["gmail", "googlecalendar"]
        )

    dispatch.assert_awaited_once()
    assert dispatch.await_args.kwargs["toolkits"] == ["gmail", "googlecalendar"]
    assert f'"execution_id": "{execution_id}"' in result
    assert '"state": "started"' in result


async def test_interaction_output_is_typed_and_agent_has_only_orchestration_tools() -> None:
    deps = _deps()
    with agent_interaction.override(model=TestModel(call_tools=[])):
        result = await agent_interaction.run("oi", deps=deps)

    assert isinstance(result.output, InteractionOutput)
    assert result.output.state in {"done", "waiting_execution", "silent"}
    tool_names = set(agent_interaction._function_toolset.tools)  # type: ignore[attr-defined]
    assert tool_names == {
        "send_message_to_user",
        "request_integration",
        "wait",
        "dispatch_execution",
        "cancel_execution",
        "confirm_email_send",
        "confirm_event_create",
    }
    assert "ask_execution" not in INTERACTION_INSTRUCTIONS
    assert INTERACTION_INSTRUCTIONS.count("<contexto interno>") >= 1
    assert "tavily_search" not in INTERACTION_INSTRUCTIONS
    assert "connected_toolkits" not in InteractionDeps.__dataclass_fields__


async def test_execution_result_hides_user_turn_tools_from_actual_model_schema() -> None:
    model = TestModel(call_tools=[])
    with agent_interaction.override(model=model):
        await agent_interaction.run("resultado interno", deps=_deps("execution_result"))

    tool_names = {
        tool.name for tool in model.last_model_request_parameters.function_tools
    }
    assert tool_names == {"send_message_to_user", "request_integration", "wait"}


async def test_execution_result_prompt_uses_exact_pending_action_summary() -> None:
    deps = _deps("execution_result")
    deps.pending_action_summary = "id=abc; tipo=create_event; título=Consulta"
    deps.internal_event_summary = (
        '{"status": "succeeded", "result": {"summary": "Evento encenado"}}'
    )
    model = TestModel(call_tools=[])
    with agent_interaction.override(model=model):
        result = await agent_interaction.run("resultado interno", deps=deps)

    injected = "\n".join(
        part.content
        for part in result.all_messages()[0].parts
        if getattr(part, "part_kind", None) == "system-prompt"
        and "Relógio local da pessoa" in getattr(part, "content", "")
    )
    assert "Modo de resultado de execução" in injected
    assert deps.pending_action_summary in injected
    assert INTERNAL_CONTEXT_OPEN not in injected
    assert deps.internal_event_summary not in injected


def test_wrap_internal_context_uses_xml_tags() -> None:
    wrapped = wrap_internal_context('{"status": "succeeded"}')
    assert wrapped.startswith("<contexto interno>\n")
    assert wrapped.endswith("\n</contexto interno>")
    assert '{"status": "succeeded"}' in wrapped


def test_inbound_history_splits_on_last_user_and_drops_later_assistant() -> None:
    history = [
        {"role": "user", "content": "marca consulta"},
        {"role": "assistant", "content": "vou cuidar disso"},
    ]
    prompt, prior = history_to_prompt_and_messages(history)
    assert prompt == "marca consulta"
    assert prior == []


def test_full_history_keeps_assistant_after_last_user() -> None:
    history = [
        {"role": "user", "content": "marca consulta"},
        {"role": "assistant", "content": "vou cuidar disso"},
    ]
    messages = history_to_messages(history)
    assert messages[0].parts[0].content == "marca consulta"
    assert messages[1].parts[0].content == "vou cuidar disso"


async def test_execution_reentry_puts_result_after_full_visible_history() -> None:
    history = [
        {"role": "user", "content": "marca uma consulta com a ana amanhã 14h"},
        {"role": "assistant", "content": "vou cuidar disso"},
    ]
    summary = '{"status": "succeeded", "result": {"summary": "Evento encenado"}}'
    run = AsyncMock(return_value=SimpleNamespace(output=InteractionOutput(state="done")))

    with (
        patch("app.agent.interaction.contact_interaction_lock", _no_lock),
        patch(
            "app.agent.interaction._load_event_context",
            AsyncMock(
                return_value=(
                    SimpleNamespace(tz="America/Sao_Paulo"),
                    history,
                    ("Gmail",),
                    "nenhuma",
                    "nenhuma",
                )
            ),
        ),
        patch.object(agent_interaction, "run", run),
    ):
        await run_interaction_event(
            contact_id=7,
            phone="15551234567",
            provider_message_id="execution:abc",
            internal_event_summary=summary,
            event_kind="execution_result",
        )

    assert run.await_args.args[0] == wrap_internal_context(summary)
    message_history = run.await_args.kwargs["message_history"]
    assert [part.content for part in message_history[0].parts] == [
        "marca uma consulta com a ana amanhã 14h"
    ]
    assert [part.content for part in message_history[1].parts] == ["vou cuidar disso"]


async def test_inbound_still_uses_last_user_as_current_prompt() -> None:
    history = [
        {"role": "user", "content": "oi"},
        {"role": "assistant", "content": "e aí"},
        {"role": "user", "content": "marca consulta"},
        {"role": "assistant", "content": "vou cuidar disso"},
    ]
    run = AsyncMock(return_value=SimpleNamespace(output=InteractionOutput(state="silent")))

    with (
        patch("app.agent.interaction.contact_interaction_lock", _no_lock),
        patch(
            "app.agent.interaction._load_event_context",
            AsyncMock(return_value=(
                SimpleNamespace(tz="America/Sao_Paulo"),
                history,
                (),
                "nenhuma",
                "nenhuma",
            )),
        ),
        patch.object(agent_interaction, "run", run),
    ):
        await run_interaction_event(
            contact_id=7,
            phone="15551234567",
            provider_message_id="SM-in",
            event_kind="user_inbound",
        )

    assert run.await_args.args[0] == "marca consulta"
    message_history = run.await_args.kwargs["message_history"]
    assert [part.content for part in message_history[0].parts] == ["oi"]
    assert [part.content for part in message_history[1].parts] == ["e aí"]


async def test_user_inbound_exposes_dispatch_and_confirmation_tools() -> None:
    model = TestModel(call_tools=[])
    deps = _deps("user_inbound")
    deps.pending_action_kinds = {
        PendingActionKind.SEND_EMAIL,
        PendingActionKind.CREATE_EVENT,
    }
    with agent_interaction.override(model=model):
        await agent_interaction.run("oi", deps=deps)

    tool_names = {
        tool.name for tool in model.last_model_request_parameters.function_tools
    }
    assert {"dispatch_execution", "confirm_email_send", "confirm_event_create"} <= tool_names


async def test_user_inbound_hides_confirmation_tools_without_pending_action() -> None:
    model = TestModel(call_tools=[])
    with agent_interaction.override(model=model):
        await agent_interaction.run("oi", deps=_deps("user_inbound"))

    tool_names = {tool.name for tool in model.last_model_request_parameters.function_tools}
    assert "confirm_email_send" not in tool_names
    assert "confirm_event_create" not in tool_names


async def test_interaction_span_summarizes_silent_outcome() -> None:
    span = MagicMock()
    span.__enter__.return_value = span
    span.__exit__.return_value = False
    run = AsyncMock(return_value=SimpleNamespace(output=InteractionOutput(state="silent")))

    with (
        patch("app.agent.interaction.contact_interaction_lock", _no_lock),
        patch(
            "app.agent.interaction._load_event_context",
            AsyncMock(return_value=_context_result()),
        ),
        patch("app.agent.interaction.logfire.span", return_value=span) as make_span,
        patch.object(agent_interaction, "run", run),
    ):
        result = await run_interaction_event(
            contact_id=7,
            phone="15551234567",
            provider_message_id="SM-in",
            event_kind="user_inbound",
        )

    assert result == InteractionOutput(state="silent")
    interaction_call = next(
        call for call in make_span.call_args_list if call.args == ("interaction",)
    )
    assert interaction_call.kwargs["interaction_run_id"]
    span.set_attributes.assert_called_once_with(
        {
            "interaction.state": "silent",
            "interaction.event_kind": "user_inbound",
            "interaction.execution_terminal_status": None,
            "interaction.exposed_tools": [
                "cancel_execution",
                "confirm_email_send",
                "confirm_event_create",
                "dispatch_execution",
                "request_integration",
                "send_message_to_user",
                "wait",
            ],
            "interaction.model_request_count": 1,
            "interaction.agent_attempts": 1,
            "interaction.reserved_outbound_count": 0,
            "interaction.outbound_outcomes": ["none"],
            "interaction.no_outbound_reason": "agent_selected_silent",
        }
    )


async def test_hard_fuse_allows_only_five_visible_outbounds() -> None:
    deps = _deps()
    reserve = AsyncMock(return_value=(SimpleNamespace(delivery_state=None), True))
    send = AsyncMock(return_value="SM1")
    delivery = AsyncMock()
    with (
        patch("app.agent.interaction.reserve_interaction_outbound", reserve),
        patch("app.agent.interaction.last_inbound_at", return_value=datetime.now(timezone.utc)),
        patch("app.agent.interaction.send_text", send),
        patch("app.agent.interaction.update_interaction_outbound_delivery", delivery),
    ):
        replies = [
            await _send_visible(deps, body=f"mensagem {i}", tool_call_id=str(i))
            for i in range(1, 7)
        ]

    assert replies[-1] == "Limite de cinco mensagens visíveis neste turno atingido."
    assert reserve.await_count == 5
    assert send.await_count == 5
    assert delivery.await_count == 5


async def test_duplicate_tool_call_uses_existing_reservation_without_second_send() -> None:
    deps = _deps()
    reserve = AsyncMock(
        side_effect=[
            (SimpleNamespace(delivery_state=MessageDeliveryState.RESERVED), True),
            (SimpleNamespace(delivery_state=MessageDeliveryState.SENT), False),
        ]
    )
    send = AsyncMock(return_value="SM1")
    with (
        patch("app.agent.interaction.reserve_interaction_outbound", reserve),
        patch("app.agent.interaction.last_inbound_at", return_value=datetime.now(timezone.utc)),
        patch("app.agent.interaction.send_text", send),
        patch("app.agent.interaction.update_interaction_outbound_delivery", AsyncMock()),
    ):
        first = await _send_visible(deps, body="oi", tool_call_id="call-1")
        duplicate = await _send_visible(deps, body="oi", tool_call_id="call-1")

    assert first == "Mensagem enviada."
    assert duplicate == "Mensagem já reservada (estado: sent)."
    assert send.await_count == 1
    assert reserve.await_args_list[0].kwargs["sequence"] == 1
    assert reserve.await_args_list[1].kwargs["sequence"] == 1


async def test_two_pre_send_model_failures_send_the_fixed_fallback_once() -> None:
    fallback = AsyncMock(return_value="Mensagem enviada.")
    run = AsyncMock(side_effect=[RuntimeError("model down"), RuntimeError("still down")])
    with (
        patch("app.agent.interaction.contact_interaction_lock", _no_lock),
        patch("app.agent.interaction._load_event_context", AsyncMock(return_value=_context_result())),
        patch.object(agent_interaction, "run", run),
        patch("app.agent.interaction._send_visible", fallback),
    ):
        result = await run_interaction_event(
            contact_id=7,
            phone="15551234567",
            provider_message_id="SM-in",
        )

    assert result is None
    assert run.await_count == 2
    fallback.assert_awaited_once()
    assert fallback.await_args.kwargs["body"] == FALLBACK_REPLY


async def test_usage_limit_does_not_replay_interaction_before_fallback() -> None:
    fallback = AsyncMock(return_value="Mensagem enviada.")
    run = AsyncMock(side_effect=UsageLimitExceeded("request limit exceeded"))
    with (
        patch("app.agent.interaction.contact_interaction_lock", _no_lock),
        patch("app.agent.interaction._load_event_context", AsyncMock(return_value=_context_result())),
        patch.object(agent_interaction, "run", run),
        patch("app.agent.interaction._send_visible", fallback),
    ):
        result = await run_interaction_event(
            contact_id=7,
            phone="15551234567",
            provider_message_id="SM-in",
            event_kind="execution_result",
        )

    assert result is None
    assert run.await_count == 1
    fallback.assert_awaited_once()


async def test_reserved_outbound_stops_model_replay_and_does_not_fallback() -> None:
    async def reserve_then_fail(*args, **kwargs):
        kwargs["deps"]._reserved_sequences.add(1)
        raise RuntimeError("output validation failed")

    run = AsyncMock(side_effect=reserve_then_fail)
    fallback = AsyncMock()
    with (
        patch("app.agent.interaction.contact_interaction_lock", _no_lock),
        patch("app.agent.interaction._load_event_context", AsyncMock(return_value=_context_result())),
        patch.object(agent_interaction, "run", run),
        patch("app.agent.interaction._send_visible", fallback),
    ):
        result = await run_interaction_event(
            contact_id=7,
            phone="15551234567",
            provider_message_id="SM-in",
        )

    assert result is None
    assert run.await_count == 1
    fallback.assert_not_awaited()


async def test_out_of_window_uses_automation_or_reminder_template() -> None:
    deps = _deps()
    old_inbound = datetime.now(timezone.utc) - timedelta(hours=48)
    with (
        patch(
            "app.agent.interaction.reserve_interaction_outbound",
            AsyncMock(return_value=(SimpleNamespace(delivery_state=None), True)),
        ),
        patch("app.agent.interaction.last_inbound_at", return_value=old_inbound),
        patch("app.agent.interaction.send_text", AsyncMock()) as send_text,
        patch("app.agent.interaction.send_action_template", AsyncMock()) as send_action,
        patch(
            "app.agent.interaction.send_automation_template",
            AsyncMock(return_value="SM-auto"),
        ) as send_auto,
        patch("app.agent.interaction.send_reminder_template", AsyncMock()) as send_rem,
        patch("app.agent.interaction.update_interaction_outbound_delivery", AsyncMock()),
        patch("app.agent.interaction.settings.twilio_automation_content_sid", "HX_AUTO"),
    ):
        result = await _send_visible(deps, body="resumo pronto", tool_call_id="call-1")

    assert result == "Mensagem enviada."
    send_text.assert_not_awaited()
    send_action.assert_not_awaited()
    send_auto.assert_awaited_once_with("15551234567", "resumo pronto")
    send_rem.assert_not_awaited()


async def test_every_inbound_uses_interaction() -> None:
    from app.api.dispatch import dispatch_inbound

    interaction = AsyncMock()
    with patch("app.api.dispatch.run_interaction_event", interaction):
        await dispatch_inbound(contact_id=7, phone="15551234567", provider_message_id="SM-in")

    interaction.assert_awaited_once()
    assert interaction.await_args.kwargs["event_kind"] == "user_inbound"
    assert interaction.await_args.kwargs["internal_event_summary"] == "nova mensagem recebida"


def test_webhook_returns_200_without_awaiting_interaction() -> None:
    from app.api.main import app

    scheduled: list[object] = []

    def capture(_self, func, *args, **kwargs):
        scheduled.append((func, args, kwargs))

    interaction = AsyncMock()
    with (
        patch("app.api.main.validate_twilio_signature", return_value=True),
        patch("app.api.main._ingest_inbound", new_callable=AsyncMock) as ingest,
        patch("app.api.main.dispatch_inbound", interaction),
        patch.object(BackgroundTasks, "add_task", capture),
    ):
        ingest.return_value = {
            "ok": True,
            "dispatch": True,
            "contact_id": 7,
            "phone": "15551234567",
            "provider_message_id": "SM-in",
        }
        response = TestClient(app).post(
            "/webhook/twilio",
            data={
                "From": "whatsapp:+15551234567",
                "Body": "oi",
                "MessageSid": "SM-in",
            },
        )

    assert response.status_code == 200
    interaction.assert_not_awaited()
    assert len(scheduled) == 1


async def test_interaction_advisory_lock_uses_distinct_contact_keys() -> None:
    conn = AsyncMock()

    @asynccontextmanager
    async def connection():
        yield conn

    fake_engine = SimpleNamespace(connect=lambda: connection())
    with patch.object(session_mod, "engine", fake_engine):
        async with session_mod.contact_interaction_lock(7):
            pass
        async with session_mod.contact_interaction_lock(8):
            pass

    first = conn.execute.await_args_list[0].args[1]
    second = conn.execute.await_args_list[1].args[1]
    assert first["ns"] == second["ns"] == session_mod.INTERACTION_LOCK_NS
    assert (first["cid"], second["cid"]) == (7, 8)


@asynccontextmanager
async def _no_lock(_contact_id: int):
    yield
