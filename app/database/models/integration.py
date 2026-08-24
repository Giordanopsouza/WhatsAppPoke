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
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.models.base import Base

if TYPE_CHECKING:
    from app.database.models.contact import Contact


class IntegrationStatus(enum.StrEnum):
    ACTIVE = "active"
    REVOKED = "revoked"


class Integration(Base):
    """Per-contact SaaS connection pointer (Composio-owned credentials)."""

    __tablename__ = "integration"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'revoked')",
            name="ck_integration_status",
        ),
        UniqueConstraint(
            "contact_id",
            "provider",
            name="uq_integration_contact_provider",
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
    # Composio toolkit slug (gmail, notion, …).
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    # Composio connected-account id (ca_…) when active; null if unknown/revoked.
    external_account_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[IntegrationStatus] = mapped_column(
        Enum(
            IntegrationStatus,
            name="integration_status",
            values_callable=lambda cls: [m.value for m in cls],
            native_enum=False,
            validate_strings=True,
        ),
        nullable=False,
        server_default=text("'active'"),
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

    contact: Mapped[Contact] = relationship(back_populates="integrations")
