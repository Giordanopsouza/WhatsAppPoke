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
    Identity,
    Index,
    Integer,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.models.base import Base

if TYPE_CHECKING:
    from app.database.models.contact import Contact


class MessageDirection(enum.StrEnum):
    IN = "in"
    OUT = "out"


class MessageDeliveryState(enum.StrEnum):
    RESERVED = "reserved"
    SENT = "sent"
    FAILED = "failed"


class Message(Base):
    __tablename__ = "message"
    __table_args__ = (
        Index("ix_message_contact_id_created_at", "contact_id", "created_at"),
        Index(
            "uq_message_provider_message_id",
            "provider_message_id",
            unique=True,
            postgresql_where=text("provider_message_id IS NOT NULL"),
        ),
        CheckConstraint(
            "delivery_state IS NULL OR delivery_state IN ('reserved', 'sent', 'failed')",
            name="ck_message_delivery_state",
        ),
        Index(
            "uq_message_interaction_outbound_sequence",
            "contact_id",
            "interaction_run_id",
            "outbound_sequence",
            unique=True,
            postgresql_where=text(
                "direction = 'out' AND interaction_run_id IS NOT NULL "
                "AND outbound_sequence IS NOT NULL"
            ),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    contact_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("contact.id", ondelete="RESTRICT"),
        nullable=False,
    )

    direction: Mapped[MessageDirection] = mapped_column(
        Enum(
            MessageDirection,
            name="message_direction",
            values_callable=lambda cls: [m.value for m in cls],
            native_enum=False,
            validate_strings=True,
        ),
        nullable=False,
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)
    provider_message_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    account_sid: Mapped[str | None] = mapped_column(Text, nullable=True)
    from_address: Mapped[str | None] = mapped_column(Text, nullable=True)
    to_address: Mapped[str | None] = mapped_column(Text, nullable=True)
    num_media: Mapped[int | None] = mapped_column(Integer, nullable=True)
    media_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    media_content_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    wa_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    sms_status: Mapped[str | None] = mapped_column(Text, nullable=True)
    api_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    num_segments: Mapped[int | None] = mapped_column(Integer, nullable=True)
    profile_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    interaction_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    outbound_sequence: Mapped[int | None] = mapped_column(Integer, nullable=True)
    delivery_state: Mapped[MessageDeliveryState | None] = mapped_column(
        Enum(
            MessageDeliveryState,
            name="message_delivery_state",
            values_callable=lambda cls: [m.value for m in cls],
            native_enum=False,
            validate_strings=True,
        ),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    contact: Mapped[Contact] = relationship(back_populates="messages")
