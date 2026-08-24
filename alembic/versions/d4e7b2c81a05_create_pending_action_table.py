"""create pending_action table

Revision ID: d4e7b2c81a05
Revises: c3f8a1b92d04
Create Date: 2026-08-08 21:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "d4e7b2c81a05"
down_revision: Union[str, Sequence[str], None] = "c3f8a1b92d04"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "pending_action",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("contact_id", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "kind IN ('send_email', 'create_event')",
            name="ck_pending_action_kind",
        ),
        sa.ForeignKeyConstraint(
            ["contact_id"],
            ["contact.id"],
            name="pending_action_contact_id_fkey",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    # Newest non-expired claim: filter by contact + expires_at, order by created_at.
    op.create_index(
        "ix_pending_action_contact_created",
        "pending_action",
        ["contact_id", "created_at"],
    )

    # Lock down PostgREST Data API: RLS on, no policies for anon/authenticated.
    # FastAPI uses the privileged pooler role and continues to bypass RLS.
    op.execute("ALTER TABLE pending_action ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
            REVOKE ALL ON TABLE pending_action FROM anon;
          END IF;
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
            REVOKE ALL ON TABLE pending_action FROM authenticated;
          END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
            GRANT ALL ON TABLE pending_action TO anon;
          END IF;
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
            GRANT ALL ON TABLE pending_action TO authenticated;
          END IF;
        END $$;
        """
    )
    op.execute("ALTER TABLE pending_action DISABLE ROW LEVEL SECURITY")

    op.drop_index("ix_pending_action_contact_created", table_name="pending_action")
    op.drop_table("pending_action")
