"""Registry boundary for owned SaaS tools.

The map names only toolkits with application-owned functions. Disconnected
toolkits and providers without owned callables contribute no schemas.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from pydantic_ai.toolsets import FunctionToolset

from app.agent.calendar_tools import (
    get_event,
    list_calendars,
    list_events,
    stage_create_event,
)
from app.agent.gmail_tools import (
    create_email_draft,
    get_email,
    search_emails,
    stage_send_email,
)


OWNED_TOOLKITS = frozenset({"gmail", "googlecalendar"})
_TOOL_FACTORIES: dict[str, tuple[Any, ...]] = {
    "gmail": (search_emails, get_email, create_email_draft, stage_send_email),
    "googlecalendar": (list_calendars, list_events, get_event, stage_create_event),
}


# Collect Gmail/Calendar tool functions for the requested toolkit slugs.
def owned_tool_functions(toolkits: Iterable[str]) -> tuple[Any, ...]:
    """Return owned callables for the requested toolkit slugs, in order."""
    functions: list[Any] = []
    for toolkit in toolkits:
        if toolkit in OWNED_TOOLKITS:
            functions.extend(_TOOL_FACTORIES[toolkit])
    return tuple(functions)


# Build a Pydantic AI toolset from our owned integration tools.
def build_owned_toolset(*, active_toolkits: Iterable[str]) -> FunctionToolset[Any] | None:
    """Build only schemas for active toolkits the application explicitly owns."""
    functions = owned_tool_functions(active_toolkits)
    return FunctionToolset(functions) if functions else None
