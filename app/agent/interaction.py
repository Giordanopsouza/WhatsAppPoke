"""Contact-scoped Interaction Agent: the sole visible WhatsApp speaker."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal

import logfire
from pydantic import BaseModel
from pydantic_ai import Agent, RunContext, UsageLimits
from pydantic_ai.capabilities import ReinjectSystemPrompt
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.messages import ModelMessage
from pydantic_ai.tools import ToolDefinition
from sqlalchemy import select
from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.history import history_to_messages, history_to_prompt_and_messages
from app.agent.model import PERSONA_MODEL_SETTINGS, build_openrouter_model
from app.core.config import settings
from app.core.logutil import get_logger
from app.database.models import (
    Contact,
    ExecutionRun,
    MessageDeliveryState,
    PendingAction,
    PendingActionKind,
    PendingActionStatus,
)
from app.db import (
    SessionLocal,
    contact_interaction_lock,
    get_session,
    last_inbound_at,
    list_active_integrations,
    load_recent_messages,
    reserve_interaction_outbound,
    update_interaction_outbound_delivery,
)
from app.db.executions import ACTIVE_EXECUTION_STATUSES
from app.services.execution import cancel_execution as cancel_detached_execution
from app.services.execution import dispatch_execution as dispatch_detached_execution
from app.services.calendar_confirmation import confirm_staged_event
from app.services.email_confirmation import confirm_staged_email
from app.integrations.providers import PROVIDERS
from app.integrations.connect_link import mint_connect_link
from app.core.timeutil import resolve_tz
from app.transport.twilio_wa import (
    in_customer_service_window,
    send_action_template,
    send_automation_template,
    send_reminder_template,
    send_text,
)


log = get_logger(__name__)

MAX_VISIBLE_OUTBOUNDS = 5
FALLBACK_REPLY = "desculpa, tive um problema… tenta de novo em instantes."
INTERNAL_CONTEXT_OPEN = "<contexto interno>"
INTERNAL_CONTEXT_CLOSE = "</contexto interno>"
INTERACTION_USAGE_LIMITS = UsageLimits(request_limit=6)
InteractionEventKind = Literal["user_inbound", "execution_result", "internal_event"]

# These are deliberately kept next to the prepare hook so the span tells us
# precisely which schema was offered to the model for each event kind.
USER_INBOUND_ONLY_TOOLS = frozenset(
    {
        "dispatch_execution",
        "cancel_execution",
        "confirm_email_send",
        "confirm_event_create",
    }
)


class InteractionOutput(BaseModel):
    """Internal outcome; this object is never rendered to WhatsApp."""

    state: Literal["done", "waiting_execution", "silent"]


@dataclass
class InteractionDeps:
    """Per-event state, including the idempotent outbound sequence allocator."""

    contact_id: int
    phone: str
    tz: str
    interaction_run_id: uuid.UUID
    session_factory: async_sessionmaker[AsyncSession]
    event_kind: InteractionEventKind
    # Conversation context only. Execution owns any concrete tool scope.
    connected_integrations: tuple[str, ...] = ()
    pending_action_summary: str = "nenhuma"
    active_execution_summary: str = "nenhuma"
    internal_event_summary: str = "evento de conversa recebido"
    inbound_turn_id: str = ""
    _sequence_by_tool_call: dict[str, int] = field(default_factory=dict)
    _reserved_sequences: set[int] = field(default_factory=set)
    _outbound_outcomes: list[str] = field(default_factory=list)
    pending_action_kinds: frozenset[PendingActionKind] = frozenset()
    dispatch_outcome: str | None = None
    dispatch_has_active_run: bool = False

    # Assign a stable sequence number for each outbound message in this turn.
    def sequence_for(self, tool_call_id: str | None) -> int:
        """Keep an LLM tool retry on its original persistence sequence."""
        key = tool_call_id or f"call-{len(self._sequence_by_tool_call) + 1}"
        if key not in self._sequence_by_tool_call:
            self._sequence_by_tool_call[key] = len(self._sequence_by_tool_call) + 1
        return self._sequence_by_tool_call[key]

    @property
    # True once we've reserved at least one WhatsApp message this turn.
    def has_reserved_visible_outbound(self) -> bool:
        return bool(self._reserved_sequences)

    def record_outbound_outcome(self, outcome: str) -> None:
        """Retain compact send decisions for the enclosing Interaction span."""
        self._outbound_outcomes.append(outcome)

INTERACTION_INSTRUCTIONS = Path(__file__).with_name("interaction_prompt.md").read_text(
    encoding="utf-8"
).strip()


agent_interaction: Agent[InteractionDeps, InteractionOutput] = Agent(
    model=build_openrouter_model(settings.openrouter_chat_model),
    name="agent_interaction",
    deps_type=InteractionDeps,
    system_prompt=INTERACTION_INSTRUCTIONS,
    output_type=InteractionOutput,
    model_settings=PERSONA_MODEL_SETTINGS,
    capabilities=[ReinjectSystemPrompt()],
)


def _only_for_user_inbound(
    ctx: RunContext[InteractionDeps], tool_def: ToolDefinition
) -> ToolDefinition | None:
    """Hide tools that need a fresh person-authored turn from result re-entry."""
    return tool_def if ctx.deps.event_kind == "user_inbound" else None


def _confirmation_tool_for(kind: PendingActionKind):
    """Expose a confirm tool only for a persisted compatible proposal."""
    def prepare(
        ctx: RunContext[InteractionDeps], tool_def: ToolDefinition
    ) -> ToolDefinition | None:
        if ctx.deps.event_kind != "user_inbound" or kind not in ctx.deps.pending_action_kinds:
            return None
        return tool_def
    return prepare


def _exposed_tool_names(event_kind: InteractionEventKind) -> list[str]:
    """Return the function-tool schema names expected for this event kind."""
    names = set(agent_interaction._function_toolset.tools)  # type: ignore[attr-defined]
    if event_kind != "user_inbound":
        names -= USER_INBOUND_ONLY_TOOLS
    return sorted(names)


def _terminal_execution_status(
    event_kind: InteractionEventKind, internal_event_summary: str
) -> str | None:
    """Extract a safe status label for tracing without recording event content."""
    if event_kind != "execution_result":
        return None
    try:
        parsed = json.loads(internal_event_summary)
        status = parsed.get("status") if isinstance(parsed, dict) else None
    except (TypeError, json.JSONDecodeError):
        return None
    return status if isinstance(status, str) else None


def _model_request_count(result: object, fallback: int) -> int:
    """Read Pydantic AI usage when available; keep mocked runtime tests simple."""
    usage = getattr(result, "usage", None)
    if not callable(usage):
        return fallback
    return usage().requests


def wrap_internal_context(summary: str) -> str:
    """Wrap an internal event so the model can tell it apart from chat text."""
    return f"{INTERNAL_CONTEXT_OPEN}\n{summary}\n{INTERNAL_CONTEXT_CLOSE}"


@agent_interaction.system_prompt
# Add contact context (timezone, integrations, pending actions) to the prompt.
def inject_interaction_context(ctx: RunContext[InteractionDeps]) -> str:
    """Give the model contact context without making internal events chat text."""
    deps = ctx.deps
    integrations = ", ".join(deps.connected_integrations) or "nenhuma"
    now = datetime.now(resolve_tz(deps.tz))
    context = (
        f"Relógio local da pessoa: {now.strftime('%d/%m/%Y %H:%M')} ({deps.tz}).\n"
        f"Integrações conectadas: {integrations}.\n"
        f"Ações pendentes de confirmação: {deps.pending_action_summary}.\n"
        f"Execuções ativas: {deps.active_execution_summary}."
    )
    if deps.event_kind == "execution_result":
        context += (
            "\nModo de resultado de execução: comunique somente o resultado "
            "terminal desta execução. Não reinicie trabalho nem confirme ações. "
            "Se houver uma ação pendente, mostre exatamente este resumo e peça "
            "uma confirmação em uma mensagem posterior da pessoa: "
            f"{deps.pending_action_summary}."
        )
    else:
        context += f"\n{wrap_internal_context(deps.internal_event_summary)}"
    return context


def _prompt_and_history(
    event_kind: InteractionEventKind,
    history: list[dict[str, str]],
    internal_event_summary: str,
) -> tuple[str, list[ModelMessage]]:
    """Inbound splits on the last user turn; execution re-entry keeps full history."""
    if event_kind == "execution_result":
        return (
            wrap_internal_context(internal_event_summary),
            history_to_messages(history),
        )
    return history_to_prompt_and_messages(history)


# Reserve, send, and persist one WhatsApp message (idempotent per sequence).
async def _send_visible(
    deps: InteractionDeps,
    *,
    body: str,
    tool_call_id: str | None,
) -> str:
    """Reserve, send once, and persist delivery for one Interaction sequence.
    
    Outside the 24-hour WhatsApp customer service window, free-form text is
    disallowed by Meta and rejected by Twilio. In that scenario, approved
    Twilio Content Templates must be used.
    """
    message = body.strip()
    if not message:
        deps.record_outbound_outcome("skipped_empty")
        return "A mensagem está vazia; não foi enviada."

    sequence = deps.sequence_for(tool_call_id)
    if sequence > MAX_VISIBLE_OUTBOUNDS:
        deps.record_outbound_outcome("skipped_limit")
        return "Limite de cinco mensagens visíveis neste turno atingido."

    async with deps.session_factory() as session:
        row, created = await reserve_interaction_outbound(
            session,
            contact_id=deps.contact_id,
            interaction_run_id=deps.interaction_run_id,
            sequence=sequence,
            body=message,
        )
        inbound_at = await last_inbound_at(session, contact_id=deps.contact_id)
        await session.commit()

    deps._reserved_sequences.add(sequence)
    if not created:
        # The prior invocation already owns this side effect. In particular, a
        # process failure after reservation must not make a second Twilio send.
        deps.record_outbound_outcome("deduplicated")
        return f"Mensagem já reservada (estado: {row.delivery_state or 'reserved'})."

    in_window = in_customer_service_window(inbound_at)

    try:
        span_name = (
            "interaction.first_visible_outbound"
            if sequence == 1
            else "interaction.visible_outbound"
        )
        with logfire.span(
            span_name,
            contact_id=deps.contact_id,
            interaction_run_id=str(deps.interaction_run_id),
            sequence=sequence,
            in_customer_service_window=in_window,
        ) as outbound_span:
            if in_window:
                outbound_span.set_attribute("interaction.outbound.channel", "free_form")
                provider_message_id = await send_text(deps.phone, message)
            else:
                if deps.pending_action_summary != "nenhuma":
                    outbound_span.set_attribute(
                        "interaction.outbound.channel", "action_template"
                    )
                    provider_message_id = await send_action_template(deps.phone, message)
                elif settings.twilio_automation_content_sid:
                    outbound_span.set_attribute(
                        "interaction.outbound.channel", "automation_template"
                    )
                    provider_message_id = await send_automation_template(deps.phone, message)
                else:
                    outbound_span.set_attribute(
                        "interaction.outbound.channel", "reminder_template"
                    )
                    provider_message_id = await send_reminder_template(deps.phone, message)
            outbound_span.set_attribute("interaction.outbound.status", "provider_accepted")
            outbound_span.set_attribute("provider_message_id", provider_message_id)
    except Exception:
        deps.record_outbound_outcome("send_failed")
        log.exception(
            "interaction_outbound_send_failed",
            extra={
                "event": "interaction_outbound_send_failed",
                "contact_id": deps.contact_id,
                "interaction_run_id": str(deps.interaction_run_id),
                "sequence": sequence,
                "in_window": in_window,
            },
        )
        async with deps.session_factory() as session:
            await update_interaction_outbound_delivery(
                session,
                contact_id=deps.contact_id,
                interaction_run_id=deps.interaction_run_id,
                sequence=sequence,
                delivery_state=MessageDeliveryState.FAILED,
            )
            await session.commit()
        return "A mensagem não pôde ser entregue; não tente reenviar neste turno."

    try:
        async with deps.session_factory() as session:
            await update_interaction_outbound_delivery(
                session,
                contact_id=deps.contact_id,
                interaction_run_id=deps.interaction_run_id,
                sequence=sequence,
                delivery_state=MessageDeliveryState.SENT,
                provider_message_id=provider_message_id,
            )
            await session.commit()
    except Exception:
        # The reservation still prevents an accidental duplicate after a
        # successful Twilio side effect; operational recovery can reconcile it.
        log.exception(
            "interaction_outbound_delivery_persist_failed",
            extra={
                "event": "interaction_outbound_delivery_persist_failed",
                "contact_id": deps.contact_id,
                "interaction_run_id": str(deps.interaction_run_id),
                "sequence": sequence,
            },
        )
        deps.record_outbound_outcome("sent_persist_failed")
        return "Mensagem enviada, mas o registro de entrega ficou pendente."

    deps.record_outbound_outcome("sent")
    return "Mensagem enviada."


@agent_interaction.tool
async def send_message_to_user(
    ctx: RunContext[InteractionDeps], message: str
) -> str:
    """Send one concise visible WhatsApp message to the current person."""
    return await _send_visible(
        ctx.deps,
        body=message,
        tool_call_id=ctx.tool_call_id,
    )


@agent_interaction.tool(name="request_integration")
async def request_interaction_integration(
    ctx: RunContext[InteractionDeps], provider: str
) -> str:
    """Create a signed link to connect one supported app for this person."""
    return await mint_connect_link(
        contact_id=ctx.deps.contact_id,
        session_factory=ctx.deps.session_factory,
        provider=provider,
    )


@agent_interaction.tool
async def wait(_ctx: RunContext[InteractionDeps]) -> str:
    """End this interaction without sending another visible message."""
    return "Aguardando; encerre com o estado internal silent ou waiting_execution."


@agent_interaction.tool(prepare=_only_for_user_inbound)
async def dispatch_execution(
    ctx: RunContext[InteractionDeps],
    goal: str,
    toolkits: list[str] | None = None,
) -> str:
    """Start one detached execution with every requested connected toolkit."""
    if ctx.deps.event_kind != "user_inbound":
        return '{"state":"unavailable","detail":"dispatch requires a new user message"}'
    outcome = await dispatch_detached_execution(
        contact_id=ctx.deps.contact_id,
        tz=ctx.deps.tz,
        goal=goal,
        toolkits=toolkits,
        session_factory=ctx.deps.session_factory,
        source_interaction_run_id=ctx.deps.interaction_run_id,
    )
    ctx.deps.dispatch_outcome = outcome.state
    # A regular dispatch dedupe only returns an active run; terminal dedupe is
    # not a wake-up source and must never justify waiting.
    ctx.deps.dispatch_has_active_run = outcome.state in {"started", "deduped"}
    return outcome.as_tool_result()


@agent_interaction.tool(prepare=_only_for_user_inbound)
async def cancel_execution(
    ctx: RunContext[InteractionDeps], execution_id: str
) -> str:
    """Request cancellation of one active detached execution by its id."""
    try:
        execution_run_id = uuid.UUID(execution_id)
    except ValueError:
        return '{"state":"unavailable","detail":"invalid execution id"}'
    outcome = await cancel_detached_execution(
        contact_id=ctx.deps.contact_id,
        execution_run_id=execution_run_id,
        session_factory=ctx.deps.session_factory,
    )
    return outcome.as_tool_result()


@agent_interaction.tool(prepare=_confirmation_tool_for(PendingActionKind.SEND_EMAIL))
async def confirm_email_send(
    ctx: RunContext[InteractionDeps], action_id: str | None = None
) -> str:
    """Send a staged Gmail draft after the current user explicitly confirms it.

    Interpret confirmation from the current user message and conversation
    history. Never call on an execution-result event. If several actions are
    pending, ask which one; after clarification pass its action_id.
    """
    if ctx.deps.event_kind != "user_inbound" or not ctx.deps.inbound_turn_id:
        return '{"state":"unavailable","detail":"confirmation requires a new user message"}'
    parsed_action_id: uuid.UUID | None = None
    if action_id is not None:
        try:
            parsed_action_id = uuid.UUID(action_id)
        except ValueError:
            return '{"state":"unavailable","detail":"invalid action id"}'
    with logfire.span(
        "pending_action_confirmation",
        contact_id=ctx.deps.contact_id,
        kind="send_email",
    ):
        outcome = await confirm_staged_email(
            contact_id=ctx.deps.contact_id,
            inbound_turn_id=ctx.deps.inbound_turn_id,
            session_factory=ctx.deps.session_factory,
            action_id=parsed_action_id,
        )
    return json.dumps(
        {
            "state": outcome.state,
            "detail": outcome.detail,
            "action_id": outcome.action_id,
        },
        ensure_ascii=False,
    )


@agent_interaction.tool(prepare=_confirmation_tool_for(PendingActionKind.CREATE_EVENT))
async def confirm_event_create(
    ctx: RunContext[InteractionDeps], action_id: str | None = None
) -> str:
    """Create a staged Calendar event after the current user explicitly confirms it.

    Interpret confirmation from the current user message and conversation
    history. Never call on an execution-result event. If several actions are
    pending, ask which one; after clarification pass its action_id.
    """
    if ctx.deps.event_kind != "user_inbound" or not ctx.deps.inbound_turn_id:
        return '{"state":"unavailable","detail":"confirmation requires a new user message"}'
    parsed_action_id: uuid.UUID | None = None
    if action_id is not None:
        try:
            parsed_action_id = uuid.UUID(action_id)
        except ValueError:
            return '{"state":"unavailable","detail":"invalid action id"}'
    with logfire.span(
        "pending_action_confirmation",
        contact_id=ctx.deps.contact_id,
        kind="create_event",
    ):
        outcome = await confirm_staged_event(
            contact_id=ctx.deps.contact_id,
            inbound_turn_id=ctx.deps.inbound_turn_id,
            session_factory=ctx.deps.session_factory,
            action_id=parsed_action_id,
        )
    return json.dumps(
        {
            "state": outcome.state,
            "detail": outcome.detail,
            "action_id": outcome.action_id,
        },
        ensure_ascii=False,
    )


# Summarize pending email/calendar actions for the Interaction system prompt.
def pending_action_prompt_summary(rows: list[PendingAction]) -> str:
    """Compact pending-action context for the Interaction prompt, never logs."""
    parts: list[str] = []
    for row in rows:
        if row.kind == PendingActionKind.CREATE_EVENT:
            parts.append(
                "id={id}; tipo={kind}; título={title}; início={start}".format(
                    id=row.id,
                    kind=row.kind,
                    title=" ".join(str(row.payload.get("title", "")).split())[:120],
                    start=" ".join(str(row.payload.get("start", "")).split())[:64],
                )
            )
            continue
        parts.append(
            "id={id}; tipo={kind}; para={to}; assunto={subject}".format(
                id=row.id,
                kind=row.kind,
                to=" ".join(str(row.payload.get("to", "")).split())[:120],
                subject=" ".join(str(row.payload.get("subject", "")).split())[:120],
            )
        )
    return "; ".join(parts) or "nenhuma"


def _confirmation_message(rows: list[PendingAction]) -> str:
    """Render persisted staged writes without exposing internal identifiers."""
    if len(rows) > 1:
        return "Tenho mais de uma ação aguardando confirmação. Diga qual delas você quer confirmar."
    row = rows[0]
    payload = row.payload
    if row.kind == PendingActionKind.CREATE_EVENT:
        title = " ".join(str(payload.get("title", "evento")).split())[:120]
        start = " ".join(str(payload.get("start", "horário combinado")).split())[:80]
        end = " ".join(str(payload.get("end", "")).split())[:80]
        details = f"{title}, em {start}" + (f" até {end}" if end else "")
        return f"Posso criar o evento {details}?"
    recipients = ", ".join(str(item) for item in payload.get("to", []) if item)
    if not recipients:
        recipients = "os destinatários informados"
    subject = " ".join(str(payload.get("subject", "(sem assunto)")).split())[:120]
    body = " ".join(str(payload.get("body", payload.get("draft", ""))).split())[:180]
    draft = f" Rascunho: {body}" if body else ""
    return f"Posso enviar o e-mail para {recipients}, assunto “{subject}”?{draft}"


def _execution_id_from_summary(summary: str) -> uuid.UUID | None:
    try:
        value = json.loads(summary).get("execution_id")
        return uuid.UUID(value) if isinstance(value, str) else None
    except (AttributeError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _result_fallback(summary: str) -> str:
    """Make a compact, safe terminal outcome when the model stays silent."""
    try:
        payload = json.loads(summary)
    except (TypeError, json.JSONDecodeError):
        return "Não consegui concluir essa tarefa. Tente de novo em instantes."
    if not isinstance(payload, dict):
        return "Não consegui concluir essa tarefa. Tente de novo em instantes."
    status = payload.get("status")
    result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
    text = " ".join(str(result.get("summary", "")).split())[:400]
    if text:
        if status == "failed" and result.get("outcome") == "needs_input":
            return text
        if status == "succeeded":
            return text
        return f"Não consegui concluir: {text}"
    return {
        "timed_out": "A tarefa demorou mais do que o esperado e não foi concluída.",
        "cancelled": "A tarefa foi cancelada.",
        "failed": "Não consegui concluir essa tarefa.",
    }.get(status, "Não consegui concluir essa tarefa. Tente de novo em instantes.")


async def _matching_pending_actions(
    *, contact_id: int, execution_run_id: uuid.UUID | None
) -> list[PendingAction]:
    if execution_run_id is None:
        return []
    async with get_session() as session:
        rows = list((await session.scalars(
            select(PendingAction).where(
                PendingAction.contact_id == contact_id,
                PendingAction.source_execution_run_id == execution_run_id,
                PendingAction.status == PendingActionStatus.PENDING,
                PendingAction.expires_at > func.now(),
            ).order_by(PendingAction.created_at.desc())
        )).all())
        await session.commit()
    return rows


# Turn integration rows into human-readable names for the prompt.
def _connected_integration_names(rows: list[object]) -> tuple[str, ...]:
    slugs = tuple(getattr(row, "provider") for row in rows)
    return tuple(
        PROVIDERS[slug].display_name if slug in PROVIDERS else slug for slug in slugs
    )


# Load contact, chat history, integrations, and pending actions for one turn.
async def _load_event_context(
    *, contact_id: int
) -> tuple[Contact | None, list[dict[str, str]], tuple[str, ...], str, str]:
    """Load only contact-scoped visible/contextual rows for an Interaction."""
    async with get_session() as session:
        contact = await session.get(Contact, contact_id)
        if contact is None:
            await session.commit()
            return None, [], (), "nenhuma", "nenhuma"

        history = await load_recent_messages(session, contact_id)
        integrations = await list_active_integrations(session, contact_id=contact_id)
        pending = list(
            (
                await session.scalars(
                    select(PendingAction).where(
                        PendingAction.contact_id == contact_id,
                        PendingAction.status.in_(
                            (PendingActionStatus.PENDING, PendingActionStatus.CLAIMED)
                        ),
                    )
                )
            ).all()
        )
        active_runs = list(
            await session.execute(
                select(
                    ExecutionRun.id,
                    ExecutionRun.goal,
                    ExecutionRun.toolkit_scope,
                    ExecutionRun.status,
                    ExecutionRun.started_at,
                    ExecutionRun.created_at,
                )
                .where(
                    ExecutionRun.contact_id == contact_id,
                    ExecutionRun.status.in_(ACTIVE_EXECUTION_STATUSES),
                )
                .order_by(ExecutionRun.created_at.desc())
                .limit(2)
            )
        )
        await session.commit()

    pending_summary = pending_action_prompt_summary(pending)
    active_summary = "; ".join(
        "id={id}; objetivo={goal}; toolkit={toolkit}; status={status}; início={started}".format(
            id=run_id,
            goal=" ".join(goal.split())[:120],
            toolkit=", ".join(toolkit_scope) or "local",
            status=status,
            started=(started_at or created_at).isoformat(),
        )
        for run_id, goal, toolkit_scope, status, started_at, created_at in active_runs
    ) or "nenhuma"
    return (
        contact,
        history,
        _connected_integration_names(integrations),
        pending_summary,
        active_summary,
    )


# Main entry: run one locked Interaction turn and optionally send WhatsApp replies.
async def run_interaction_event(
    *,
    contact_id: int,
    phone: str,
    provider_message_id: str,
    internal_event_summary: str = "nova mensagem recebida",
    event_kind: InteractionEventKind = "user_inbound",
    require_visible_delivery: bool = False,
) -> InteractionOutput | None:
    """Run one locked Interaction event after the Twilio webhook has returned.

    The only use of the typed output is runtime control and observability; it
    is never passed to Twilio. A retry is permitted only before any visible
    outbound reservation exists.
    """
    interaction_run_id = uuid.uuid4()
    async with contact_interaction_lock(contact_id):
        with logfire.span(
            "interaction",
            contact_id=contact_id,
            interaction_run_id=str(interaction_run_id),
            provider_message_id=provider_message_id,
            event_kind=event_kind,
            execution_terminal_status=_terminal_execution_status(
                event_kind, internal_event_summary
            ),
            interaction_exposed_tools=_exposed_tool_names(event_kind),
        ) as interaction_span:
            contact, history, integrations, pending, active = await _load_event_context(
                contact_id=contact_id
            )
            if contact is None or not history:
                interaction_span.set_attribute("interaction.state", "skipped")
                interaction_span.set_attribute(
                    "interaction.skip_reason",
                    "contact_not_found" if contact is None else "history_empty",
                )
                return None

            deps = InteractionDeps(
                contact_id=contact_id,
                phone=phone,
                tz=contact.tz,
                interaction_run_id=interaction_run_id,
                session_factory=SessionLocal,
                connected_integrations=integrations,
                pending_action_summary=pending,
                active_execution_summary=active,
                # Provider ids are useful for logs/dedupe but are not prompt text.
                internal_event_summary=internal_event_summary,
                inbound_turn_id=(
                    provider_message_id if event_kind == "user_inbound" else ""
                ),
                event_kind=event_kind,
            )
            if event_kind == "user_inbound":
                # The summary is created from the same unexpired pending rows
                # loaded under the Interaction lock; use it only to select
                # schemas, never to execute a confirmation.
                deps.pending_action_kinds = frozenset(
                    kind for kind in PendingActionKind
                    if f"tipo={kind}" in pending
                )

            # Confirmation is a code-owned terminal delivery. It is based on
            # exactly the Execution that staged the proposal, never another
            # old proposal for the same contact.
            matching_actions = await _matching_pending_actions(
                contact_id=contact_id,
                execution_run_id=_execution_id_from_summary(internal_event_summary)
                if event_kind == "execution_result" else None,
            )
            if matching_actions:
                await _send_visible(
                    deps,
                    body=_confirmation_message(matching_actions),
                    tool_call_id="pending-action-confirmation",
                )
                delivered = any(
                    outcome in {"sent", "deduplicated", "sent_persist_failed"}
                    for outcome in deps._outbound_outcomes
                )
                interaction_span.set_attributes({
                    "interaction.state": "done",
                    "interaction.delivery_path": "pending_action",
                    "interaction.outbound_outcomes": deps._outbound_outcomes or ["none"],
                    "interaction.processed_decision": delivered,
                })
                return InteractionOutput(state="done") if delivered or not require_visible_delivery else None
            user_prompt, message_history = _prompt_and_history(
                event_kind, history, internal_event_summary
            )

            for attempt in range(2):
                try:
                    with logfire.span(
                        "interaction.agent_attempt",
                        attempt=attempt,
                    ):
                        result = await agent_interaction.run(
                            user_prompt,
                            message_history=message_history or None,
                            deps=deps,
                            usage_limits=INTERACTION_USAGE_LIMITS,
                        )
                    interaction_attributes = {
                        "interaction.state": result.output.state,
                        "interaction.event_kind": event_kind,
                        "interaction.execution_terminal_status": _terminal_execution_status(
                            event_kind, internal_event_summary
                        ),
                        "interaction.exposed_tools": _exposed_tool_names(event_kind),
                        "interaction.model_request_count": _model_request_count(
                            result, attempt + 1
                        ),
                        "interaction.agent_attempts": attempt + 1,
                        "interaction.reserved_outbound_count": len(
                            deps._reserved_sequences
                        ),
                        "interaction.outbound_outcomes": deps._outbound_outcomes
                        or ["none"],
                    }
                    no_outbound_reason: str | None = None
                    effective_state = result.output.state
                    if (
                        require_visible_delivery
                        and event_kind == "execution_result"
                        and not deps.has_reserved_visible_outbound
                    ):
                        await _send_visible(
                            deps,
                            body=_result_fallback(internal_event_summary),
                            tool_call_id="execution-result-fallback",
                        )
                        effective_state = "done"
                        interaction_attributes["interaction.delivery_path"] = "result_fallback"
                    elif (
                        event_kind == "user_inbound"
                        and result.output.state == "waiting_execution"
                        and not (deps.dispatch_has_active_run or active != "nenhuma")
                    ):
                        effective_state = "done"
                        interaction_attributes["interaction.invalid_state_reason"] = (
                            "waiting_execution_without_active_execution"
                        )
                    if not deps.has_reserved_visible_outbound:
                        no_outbound_reason = (
                            deps._outbound_outcomes[-1]
                            if deps._outbound_outcomes
                            else {
                                "waiting_execution": "waiting_execution",
                                "silent": "agent_selected_silent",
                                "done": "completed_without_send",
                            }[effective_state]
                        )
                        interaction_attributes["interaction.no_outbound_reason"] = (
                            no_outbound_reason
                        )
                    interaction_attributes["interaction.state"] = effective_state
                    interaction_span.set_attributes(interaction_attributes)
                    log.info(
                        "interaction_completed",
                        extra={
                            "event": "interaction_completed",
                            "contact_id": contact_id,
                            "interaction_run_id": str(interaction_run_id),
                            "provider_message_id": provider_message_id,
                            "state": effective_state,
                            "outbound_outcomes": deps._outbound_outcomes or ["none"],
                            "no_outbound_reason": no_outbound_reason,
                        },
                    )
                    delivered = any(
                        outcome in {"sent", "deduplicated", "sent_persist_failed"}
                        for outcome in deps._outbound_outcomes
                    )
                    if require_visible_delivery and event_kind == "execution_result" and not delivered:
                        interaction_span.set_attribute("interaction.processed_decision", False)
                        return None
                    return InteractionOutput(state=effective_state)
                except UsageLimitExceeded:
                    # A request-limit error cannot be repaired by replaying the
                    # same Interaction. Fall back once, provided no visible
                    # outbound was already reserved.
                    log.exception(
                        "interaction_usage_limit_exceeded",
                        extra={
                            "event": "interaction_usage_limit_exceeded",
                            "contact_id": contact_id,
                            "interaction_run_id": str(interaction_run_id),
                            "provider_message_id": provider_message_id,
                            "event_kind": event_kind,
                            "attempt": attempt,
                        },
                    )
                    if deps.has_reserved_visible_outbound:
                        interaction_span.set_attributes(
                            {
                                "interaction.state": "usage_limit_after_outbound_reservation",
                                "interaction.event_kind": event_kind,
                                "interaction.execution_terminal_status": _terminal_execution_status(
                                    event_kind, internal_event_summary
                                ),
                                "interaction.exposed_tools": _exposed_tool_names(event_kind),
                                "interaction.model_request_count": attempt + 1,
                                "interaction.agent_attempts": attempt + 1,
                                "interaction.reserved_outbound_count": len(deps._reserved_sequences),
                                "interaction.outbound_outcomes": deps._outbound_outcomes or ["reserved"],
                            }
                        )
                        return None
                    break
                except Exception:
                    log.exception(
                        "interaction_model_failed",
                        extra={
                            "event": "interaction_model_failed",
                            "contact_id": contact_id,
                            "interaction_run_id": str(interaction_run_id),
                            "provider_message_id": provider_message_id,
                            "attempt": attempt,
                        },
                    )
                    if deps.has_reserved_visible_outbound:
                        interaction_span.set_attributes(
                            {
                                "interaction.state": "failed_after_outbound_reservation",
                                "interaction.event_kind": event_kind,
                                "interaction.execution_terminal_status": _terminal_execution_status(
                                    event_kind, internal_event_summary
                                ),
                                "interaction.exposed_tools": _exposed_tool_names(event_kind),
                                "interaction.model_request_count": attempt + 1,
                                "interaction.agent_attempts": attempt + 1,
                                "interaction.reserved_outbound_count": len(
                                    deps._reserved_sequences
                                ),
                                "interaction.outbound_outcomes": deps._outbound_outcomes
                                or ["reserved"],
                            }
                        )
                        return None

            # Both pre-send model attempts failed. This path uses the same
            # reservation/send primitive and is attempted once only.
            await _send_visible(deps, body=FALLBACK_REPLY, tool_call_id="fallback")
            interaction_span.set_attributes(
                {
                    "interaction.state": "fallback",
                    "interaction.event_kind": event_kind,
                    "interaction.execution_terminal_status": _terminal_execution_status(
                        event_kind, internal_event_summary
                    ),
                    "interaction.exposed_tools": _exposed_tool_names(event_kind),
                    "interaction.agent_attempts": 2,
                    "interaction.reserved_outbound_count": len(deps._reserved_sequences),
                    "interaction.outbound_outcomes": deps._outbound_outcomes or ["none"],
                }
            )
            return None
