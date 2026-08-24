"""create job table

Revision ID: 183da715dd33
Revises: 28b0ac108edc
Create Date: 2026-08-06 11:15:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "183da715dd33"
down_revision: Union[str, Sequence[str], None] = "28b0ac108edc"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "job",
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
        sa.Column("run_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "status",
            sa.Text(),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column(
            "attempts",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "max_attempts",
            sa.Integer(),
            server_default=sa.text("5"),
            nullable=False,
        ),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "kind IN ('agent_turn', 'reminder_due', 'integration_notify')",
            name="ck_job_kind",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'done', 'dead')",
            name="ck_job_status",
        ),
        sa.ForeignKeyConstraint(
            ["contact_id"],
            ["contact.id"],
            name="job_contact_id_fkey",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    # Claim query: pending/running jobs ordered by run_at.
    op.create_index("ix_job_status_run_at", "job", ["status", "run_at"])

    # At most one pending agent turn per contact (debounce coalescing).
    op.create_index(
        "uq_job_pending_agent_turn_per_contact",
        "job",
        ["contact_id"],
        unique=True,
        postgresql_where=sa.text("status = 'pending' AND kind = 'agent_turn'"),
    )

    # Lock down PostgREST Data API: RLS on, no policies for anon/authenticated.
    # FastAPI uses the privileged pooler role and continues to bypass RLS.
    op.execute("ALTER TABLE job ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
            REVOKE ALL ON TABLE job FROM anon;
          END IF;
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
            REVOKE ALL ON TABLE job FROM authenticated;
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
            GRANT ALL ON TABLE job TO anon;
          END IF;
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
            GRANT ALL ON TABLE job TO authenticated;
          END IF;
        END $$;
        """
    )
    op.execute("ALTER TABLE job DISABLE ROW LEVEL SECURITY")

    op.drop_index(
        "uq_job_pending_agent_turn_per_contact",
        table_name="job",
        postgresql_where=sa.text("status = 'pending' AND kind = 'agent_turn'"),
    )
    op.drop_index("ix_job_status_run_at", table_name="job")
    op.drop_table("job")
