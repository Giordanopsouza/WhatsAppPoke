from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    Boolean,
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


class AutomationStatus(enum.StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    CANCELLED = "cancelled"


class AutomationLastRunStatus(enum.StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"


class Automation(Base):
    __tablename__ = "automation"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'paused', 'cancelled')",
            name="ck_automation_status",
        ),
        CheckConstraint(
            "last_run_status IS NULL OR last_run_status IN "
            "('succeeded', 'failed', 'timed_out', 'cancelled', 'skipped')",
            name="ck_automation_last_run_status",
        ),
        CheckConstraint(
            "jsonb_typeof(required_toolkits) = 'array'",
            name="ck_automation_required_toolkits_array",
        ),
        Index("ix_automation_contact_status", "contact_id", "status"),
        Index(
            "ix_automation_active_next_run",
            "next_run_at",
            postgresql_where=text("status = 'active' AND next_run_at IS NOT NULL"),
        ),
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
    name: Mapped[str] = mapped_column(Text, nullable=False)
    goal: Mapped[str] = mapped_column(Text, nullable=False)
    rrule: Mapped[str] = mapped_column(Text, nullable=False)
    timezone: Mapped[str] = mapped_column(Text, nullable=False)
    required_toolkits: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    status: Mapped[AutomationStatus] = mapped_column(
        Enum(
            AutomationStatus,
            name="automation_status",
            values_callable=lambda cls: [m.value for m in cls],
            native_enum=False,
            validate_strings=True,
        ),
        nullable=False,
        server_default=text("'active'"),
    )
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_run_status: Mapped[AutomationLastRunStatus | None] = mapped_column(
        Enum(
            AutomationLastRunStatus,
            name="automation_last_run_status",
            values_callable=lambda cls: [m.value for m in cls],
            native_enum=False,
            validate_strings=True,
        ),
        nullable=True,
    )
    last_occurrence_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_execution_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    last_run_was_catch_up: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    contact: Mapped[Contact] = relationship(back_populates="automations")
