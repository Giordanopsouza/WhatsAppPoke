"""create connect_link table

Revision ID: a1c8e4f92b07
Revises: 3d6698e2f6bd
Create Date: 2026-08-07 17:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "a1c8e4f92b07"
down_revision: Union[str, Sequence[str], None] = "3d6698e2f6bd"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "connect_link",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("contact_id", sa.BigInteger(), nullable=False),
        sa.Column("nonce", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["contact_id"],
            ["contact.id"],
            name="connect_link_contact_id_fkey",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("nonce", name="uq_connect_link_nonce"),
    )
    op.create_index(
        "ix_connect_link_contact_id",
        "connect_link",
        ["contact_id"],
    )

    # Lock down PostgREST Data API: RLS on, no policies for anon/authenticated.
    # FastAPI uses the privileged pooler role and continues to bypass RLS.
    op.execute("ALTER TABLE connect_link ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
            REVOKE ALL ON TABLE connect_link FROM anon;
          END IF;
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
            REVOKE ALL ON TABLE connect_link FROM authenticated;
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
            GRANT ALL ON TABLE connect_link TO anon;
          END IF;
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
            GRANT ALL ON TABLE connect_link TO authenticated;
          END IF;
        END $$;
        """
    )
    op.execute("ALTER TABLE connect_link DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_connect_link_contact_id", table_name="connect_link")
    op.drop_table("connect_link")
