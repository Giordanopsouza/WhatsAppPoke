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
    Integer,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.models.base import Base

if TYPE_CHECKING:
    from app.database.models.contact import Contact


class JobKind(enum.StrEnum):
    REMINDER_DUE = "reminder_due"
    INTEGRATION_NOTIFY = "integration_notify"
    AUTOMATION_DUE = "automation_due"


class JobStatus(enum.StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    DEAD = "dead"


class Job(Base):
    __tablename__ = "job"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('reminder_due', 'integration_notify', 'automation_due')",
            name="ck_job_kind",
        ),
        CheckConstraint(
            "status IN ('pending', 'running', 'done', 'dead')",
            name="ck_job_status",
        ),
        Index("ix_job_status_run_at", "status", "run_at"),
        Index(
            "uq_job_pending_automation_due",
            text("(payload->>'automation_id')"),
            unique=True,
            postgresql_where=text("status = 'pending' AND kind = 'automation_due'"),
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
    kind: Mapped[JobKind] = mapped_column(
        Enum(
            JobKind,
            name="job_kind",
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
    run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[JobStatus] = mapped_column(
        Enum(
            JobStatus,
            name="job_status",
            values_callable=lambda cls: [m.value for m in cls],
            native_enum=False,
            validate_strings=True,
        ),
        nullable=False,
        server_default=text("'pending'"),
    )
    attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    max_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("5")
    )
    locked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    contact: Mapped[Contact] = relationship(back_populates="jobs")
