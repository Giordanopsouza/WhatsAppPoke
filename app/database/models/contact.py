from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, DateTime, Identity, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.models.base import Base

if TYPE_CHECKING:
    from app.database.models.connect_link import ConnectLink
    from app.database.models.integration import Integration
    from app.database.models.execution import ExecutionRun
    from app.database.models.job import Job
    from app.database.models.message import Message
    from app.database.models.pending_action import PendingAction
    from app.database.models.automation import Automation
    from app.database.models.reminder import Reminder
    from app.database.models.task import Task


class Contact(Base):
    __tablename__ = "contact"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    phone: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    name: Mapped[str | None] = mapped_column(Text, nullable=True)
    tz: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=text("'America/Sao_Paulo'"),
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    messages: Mapped[list[Message]] = relationship(back_populates="contact")
    jobs: Mapped[list[Job]] = relationship(back_populates="contact")
    integrations: Mapped[list[Integration]] = relationship(back_populates="contact")
    connect_links: Mapped[list[ConnectLink]] = relationship(back_populates="contact")
    pending_actions: Mapped[list[PendingAction]] = relationship(
        back_populates="contact"
    )
    tasks: Mapped[list[Task]] = relationship(back_populates="contact")
    reminders: Mapped[list[Reminder]] = relationship(back_populates="contact")
    automations: Mapped[list[Automation]] = relationship(back_populates="contact")
    execution_runs: Mapped[list[ExecutionRun]] = relationship(back_populates="contact")
