from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any, TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.models.base import Base

if TYPE_CHECKING:
    from app.database.models.contact import Contact


class PendingActionKind(enum.StrEnum):
    SEND_EMAIL = "send_email"
    CREATE_EVENT = "create_event"


class PendingActionStatus(enum.StrEnum):
    """Confirmation lifecycle; terminal rows remain available for audit."""

    PENDING = "pending"
    CLAIMED = "claimed"
    EXECUTED = "executed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    FAILED = "failed"


class PendingAction(Base):
    __tablename__ = "pending_action"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('send_email', 'create_event')",
            name="ck_pending_action_kind",
        ),
        CheckConstraint(
            "status IN ('pending', 'claimed', 'executed', 'cancelled', "
            "'expired', 'failed')",
            name="ck_pending_action_status",
        ),
        Index("ix_pending_action_contact_created", "contact_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    contact_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("contact.id", ondelete="RESTRICT"),
        nullable=False,
    )
    kind: Mapped[PendingActionKind] = mapped_column(
        Enum(
            PendingActionKind,
            name="pending_action_kind",
            values_callable=lambda cls: [m.value for m in cls],
            native_enum=False,
            validate_strings=True,
        ),
        nullable=False,
    )
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )
    payload_hash: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[PendingActionStatus] = mapped_column(
        Enum(
            PendingActionStatus,
            name="pending_action_status",
            values_callable=lambda cls: [m.value for m in cls],
            native_enum=False,
            validate_strings=True,
        ),
        nullable=False,
        server_default=text("'pending'"),
    )
    # Job id of the turn that staged this row. Confirming from that same turn
    # is refused: the "yes" has to arrive as a separate WhatsApp message.
    created_turn_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_interaction_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    source_execution_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("execution_run.id", ondelete="RESTRICT"),
        nullable=True,
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    contact: Mapped[Contact] = relationship(back_populates="pending_actions")
