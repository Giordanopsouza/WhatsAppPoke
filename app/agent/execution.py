"""Reusable, non-speaking Execution Agent and its owned local-tool registry.

This module deliberately has no transport dependency.  Execution returns data
to the lifecycle service; only Interaction may decide whether to speak about
that data on WhatsApp.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel
from pydantic_ai import Agent, RunContext, UsageLimits
from pydantic_ai.capabilities import ReinjectSystemPrompt
from pydantic_ai.toolsets import FunctionToolset
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.model import build_openrouter_model
from app.agent.owned_tools import owned_tool_functions
from app.agent.tools import (
    add_task,
    cancel_reminder,
    complete_task,
    list_reminders,
    list_tasks,
    set_reminder,
    tavily_search,
)
from app.agent.automation_tools import (
    cancel_automation,
    create_automation,
    list_automations,
    pause_automation,
    resume_automation,
)
from app.core.config import settings
from app.core.timeutil import resolve_tz


LOCAL_TOOLKIT = "local"
EXECUTION_USAGE_LIMITS = UsageLimits(request_limit=6)


class ExecutionOutcome(BaseModel):
    """Terminal outcome of one Execution run; never rendered to WhatsApp.

    `status` drives `execution_run.status` so the re-entry Interaction sees
    whether the goal was actually completed. `summary` is the short factual
    text the Interaction relays to the person.
    """

    status: Literal["succeeded", "failed", "needs_input"]
    summary: str


@dataclass(frozen=True)
class ExecutionDeps:
    """One isolated Execution run's contact-scoped dependencies."""

    contact_id: int
    session_factory: async_sessionmaker[AsyncSession]
    tz: str
    execution_run_id: uuid.UUID
    goal: str = ""
    source_interaction_run_id: uuid.UUID | None = None
    toolkit_scope: tuple[str, ...] = field(default_factory=tuple)


EXECUTION_INSTRUCTIONS = Path(__file__).with_name("execution_prompt.md").read_text(
    encoding="utf-8"
).strip()


# A single reusable definition.  The per-run FunctionToolset below controls
# which schemas are actually supplied to it; do not add transport tools here.
agent_execution: Agent[ExecutionDeps, ExecutionOutcome] = Agent(
    model=build_openrouter_model(settings.openrouter_exec_model),
    name="agent_execution",
    deps_type=ExecutionDeps,
    system_prompt=EXECUTION_INSTRUCTIONS,
    tools=[],
    output_type=ExecutionOutcome,
    capabilities=[ReinjectSystemPrompt()],
)


@agent_execution.system_prompt
# Tell the execution agent the current time and which tools it may use.
def inject_execution_context(ctx: RunContext[ExecutionDeps]) -> str:
    deps = ctx.deps
    now = datetime.now(resolve_tz(deps.tz))
    scope = ", ".join(deps.toolkit_scope) or "nenhum"
    return (
        f"Relógio local: {now.strftime('%d/%m/%Y %H:%M')} ({deps.tz}).\n"
        f"Escopo de ferramentas deste trabalho: {scope}."
    )


_LOCAL_EXECUTION_TOOLS = (
    tavily_search,
    add_task,
    list_tasks,
    complete_task,
    set_reminder,
    list_reminders,
    cancel_reminder,
    create_automation,
    list_automations,
    pause_automation,
    resume_automation,
    cancel_automation,
)


# Pick which tool functions the execution agent can use for one toolkit.
def build_execution_toolset(
    *, toolkit: str | None
) -> FunctionToolset[ExecutionDeps] | None:
    """Return only schemas owned by the requested, connected domain.

    Task 041 owns the deterministic local domain. Gmail and Calendar are
    app-owned and contribute schemas only when that toolkit is requested.
    """
    if toolkit in (None, LOCAL_TOOLKIT):
        return FunctionToolset(_LOCAL_EXECUTION_TOOLS)
    functions = owned_tool_functions((toolkit,))
    return FunctionToolset(functions) if functions else None


# Like build_execution_toolset but for scheduled automations with multiple toolkits.
def build_scoped_execution_toolset(
    toolkits: Sequence[str],
) -> FunctionToolset[ExecutionDeps]:
    """Local tools plus every requested owned toolkit (scheduled automations)."""
    functions: list[Any] = list(_LOCAL_EXECUTION_TOOLS)
    owned = owned_tool_functions(toolkits)
    functions.extend(owned)
    return FunctionToolset(functions)


# Run the execution agent once with a fixed goal and tool surface.
async def run_execution_goal(
    *,
    goal: str,
    deps: ExecutionDeps,
    toolkit: str | None = None,
    toolkits: Sequence[str] | None = None,
) -> ExecutionOutcome:
    """Run one goal with a tool surface fixed before model execution."""
    if toolkits is not None:
        toolset = build_scoped_execution_toolset(toolkits)
    else:
        toolset = build_execution_toolset(toolkit=toolkit)
    result = await agent_execution.run(
        goal,
        deps=deps,
        usage_limits=EXECUTION_USAGE_LIMITS,
        toolsets=[toolset] if toolset is not None else [],
    )
    return result.output
