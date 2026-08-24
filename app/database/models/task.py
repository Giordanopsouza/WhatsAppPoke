from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

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
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.models.base import Base

if TYPE_CHECKING:
    from app.database.models.contact import Contact


class TaskStatus(enum.StrEnum):
    OPEN = "open"
    DONE = "done"


class Task(Base):
    __tablename__ = "task"
    __table_args__ = (
        CheckConstraint(
            "status IN ('open', 'done')",
            name="ck_task_status",
        ),
        Index("ix_task_contact_status_due", "contact_id", "status", "due_at"),
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
    title: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[TaskStatus] = mapped_column(
        Enum(
            TaskStatus,
            name="task_status",
            values_callable=lambda cls: [m.value for m in cls],
            native_enum=False,
            validate_strings=True,
        ),
        nullable=False,
        server_default=text("'open'"),
    )
    due_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
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

    contact: Mapped[Contact] = relationship(back_populates="tasks")
