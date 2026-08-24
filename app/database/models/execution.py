from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any, TYPE_CHECKING

from sqlalchemy import BigInteger, Boolean, CheckConstraint, DateTime, Enum, ForeignKey, Index, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.models.base import Base

if TYPE_CHECKING:
    from app.database.models.contact import Contact


class ExecutionRunStatus(enum.StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLED = "cancelled"
    ABANDONED = "abandoned"


class ExecutionRun(Base):
    __tablename__ = "execution_run"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'failed', "
            "'timed_out', 'cancel_requested', 'cancelled', 'abandoned')",
            name="ck_execution_run_status",
        ),
        Index(
            "uq_execution_run_active_contact_dedupe",
            "contact_id",
            "dedupe_key",
            unique=True,
            postgresql_where=text(
                "status IN ('pending', 'running', 'cancel_requested')"
            ),
        ),
        Index(
            "ix_execution_run_active_contact",
            "contact_id",
            "created_at",
            postgresql_where=text(
                "status IN ('pending', 'running', 'cancel_requested')"
            ),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    contact_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("contact.id", ondelete="RESTRICT"), nullable=False
    )
    goal: Mapped[str] = mapped_column(Text, nullable=False)
    toolkit_scope: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    dedupe_key: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[ExecutionRunStatus] = mapped_column(
        Enum(
            ExecutionRunStatus,
            name="execution_run_status",
            values_callable=lambda cls: [member.value for member in cls],
            native_enum=False,
            validate_strings=True,
        ),
        nullable=False,
        server_default=text("'pending'"),
    )
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    cancel_requested: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    contact: Mapped[Contact] = relationship(back_populates="execution_runs")
    events: Mapped[list[ExecutionEvent]] = relationship(back_populates="execution_run")


class ExecutionEvent(Base):
    __tablename__ = "execution_event"
    __table_args__ = (
        Index(
            "ix_execution_event_contact_unprocessed",
            "contact_id",
            "created_at",
            postgresql_where=text("processed_at IS NULL"),
        ),
        Index("ix_execution_event_run_created", "execution_run_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    contact_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("contact.id", ondelete="RESTRICT"), nullable=False
    )
    execution_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("execution_run.id", ondelete="RESTRICT"),
        nullable=False,
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    execution_run: Mapped[ExecutionRun] = relationship(back_populates="events")
