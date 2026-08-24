"""Architecture boundary for Interaction-only runtime without daily briefing."""

from __future__ import annotations

import inspect
from pathlib import Path

from app.api import dispatch
from app.database.models import JobKind
from app.worker import loop


ROOT = Path(__file__).parents[1]


def test_inbound_runtime_has_no_legacy_route_or_conversation_job() -> None:
    source = inspect.getsource(dispatch)
    assert "run_interaction_event" in source
    assert "send_text" not in source
    assert "enqueue_agent_turn" not in source
    assert "classify" not in source
    assert "ACK_BODY" not in source
    assert "briefing" not in source.lower()
    assert "get_briefing_state" not in source

    assert set(JobKind) == {
        JobKind.REMINDER_DUE,
        JobKind.INTEGRATION_NOTIFY,
        JobKind.AUTOMATION_DUE,
    }
    assert set(loop.HANDLERS) == set(JobKind)
    loop_src = inspect.getsource(loop)
    assert "AGENT_TURN" not in loop_src
    assert "agent_turn" not in loop_src
    assert "outbound_sweep" not in loop_src
    assert "outbound_due" not in loop_src
    assert "seed_outbound" not in loop_src


def test_legacy_agent_and_mcp_modules_are_gone() -> None:
    agent_dir = ROOT / "app" / "agent"
    integrations_dir = ROOT / "app" / "integrations"
    assert not any(
        (agent_dir / name).exists()
        for name in (
            "ack.py",
            "classify.py",
            "composio_mcp.py",
            "deps.py",
            "loop.py",
            "system_prompt.md",
            "tool_prompt.md",
        )
    )
    assert not (integrations_dir / "composio_policy.py").exists()


def test_briefing_runtime_is_gone() -> None:
    assert not (ROOT / "app" / "services" / "briefing.py").exists()
    assert not (ROOT / "app" / "db" / "briefing.py").exists()
    assert not (ROOT / "app" / "database" / "models" / "briefing_state.py").exists()
    assert not (ROOT / "app" / "worker" / "handlers" / "outbound_sweep.py").exists()
    assert not (ROOT / "app" / "worker" / "handlers" / "outbound_due.py").exists()

    config_src = (ROOT / "app" / "core" / "config.py").read_text(encoding="utf-8")
    env_example = (ROOT / ".env.example").read_text(encoding="utf-8")
    interaction_src = (ROOT / "app" / "agent" / "interaction.py").read_text(
        encoding="utf-8"
    )
    twilio_src = (ROOT / "app" / "transport" / "twilio_wa.py").read_text(encoding="utf-8")
    assert "TWILIO_BRIEFING_CONTENT_SID" not in config_src
    assert "twilio_briefing_content_sid" not in config_src
    assert "TWILIO_BRIEFING_CONTENT_SID" not in env_example
    assert "send_briefing_template" not in interaction_src
    assert "briefing_matinal" not in interaction_src
    assert "get_briefing_state" not in interaction_src
    assert "send_briefing_template" not in twilio_src
    assert "OUTBOUND_SWEEP" not in inspect.getsource(JobKind)
    assert "OUTBOUND_DUE" not in inspect.getsource(JobKind)
