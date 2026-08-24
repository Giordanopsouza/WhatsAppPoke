"""pending_action DB helpers — table kept for a future Composio send confirm."""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from sqlalchemy.dialects import postgresql

from app.database.models import PendingActionKind, PendingActionStatus


async def test_create_pending_action_cancels_the_previous_proposal() -> None:
    """One armed proposal per contact: an abandoned one cannot fire later."""
    from app import db as db_mod

    session = AsyncMock()
    session.add = MagicMock()  # sync on a real Session
    await db_mod.create_pending_action(
        session,
        contact_id=7,
        kind=PendingActionKind.SEND_EMAIL,
        payload={"draft_id": "d-2"},
        turn_id="job-2",
    )

    session.execute.assert_awaited_once()
    sql = str(
        session.execute.await_args.args[0].compile(dialect=postgresql.dialect())
    )
    assert sql.startswith("UPDATE pending_action")
    assert "contact_id" in sql
    staged = session.add.call_args.args[0]
    assert staged.created_turn_id == "job-2"
    assert staged.status == PendingActionStatus.PENDING
    assert len(staged.payload_hash) == 64


async def test_claim_pending_action_gates_turn_status_and_kind() -> None:
    """The confirmation gate is SQL — assert the statement carries it."""
    from app import db as db_mod

    captured: dict[str, Any] = {}

    async def _scalar(stmt: Any, **_kwargs: Any) -> None:
        captured["stmt"] = stmt
        return None

    session = AsyncMock()
    session.scalar = _scalar

    await db_mod.claim_pending_action(
        session,
        contact_id=7,
        kind=PendingActionKind.SEND_EMAIL,
        turn_id="job-1",
    )

    compiled = captured["stmt"].compile(dialect=postgresql.dialect())
    sql = str(compiled)
    assert sql.startswith("UPDATE pending_action")
    assert "RETURNING" in sql
    assert "created_turn_id IS DISTINCT FROM" in sql
    assert "expires_at >" in sql
    params = compiled.params
    assert "job-1" in params.values()
    assert PendingActionKind.SEND_EMAIL in params.values()
    assert 7 in params.values()


async def test_pending_action_failure_is_terminal() -> None:
    from app import db as db_mod

    session = AsyncMock()
    await db_mod.fail_pending_action(session, action_id=uuid.uuid4())

    compiled = session.execute.await_args.args[0].compile(dialect=postgresql.dialect())
    assert "UPDATE pending_action" in str(compiled)
    assert PendingActionStatus.FAILED in compiled.params.values()
