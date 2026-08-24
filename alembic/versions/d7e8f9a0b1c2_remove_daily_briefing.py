"""remove product-level daily briefing

Revision ID: d7e8f9a0b1c2
Revises: c5d6e7f8a9b0
Create Date: 2026-08-17 17:00:00.000000

Drain obsolete outbound_sweep / outbound_due jobs, delete seeded Briefing
Matinal automations, drop briefing_state, and require job.contact_id.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d7e8f9a0b1c2"
down_revision: Union[str, Sequence[str], None] = "c5d6e7f8a9b0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "DELETE FROM job WHERE kind IN ('outbound_sweep', 'outbound_due')"
    )
    op.execute(
        """
        DELETE FROM job
        WHERE kind = 'automation_due'
          AND (payload->>'automation_id') IN (
              SELECT id::text FROM automation WHERE name = 'Briefing Matinal'
          )
        """
    )
    op.execute("DELETE FROM automation WHERE name = 'Briefing Matinal'")

    op.drop_table("briefing_state")
    op.execute("DROP TYPE IF EXISTS briefing_cadence")

    op.drop_index(
        "uq_job_pending_outbound_due_per_contact",
        table_name="job",
        postgresql_where=sa.text("status = 'pending' AND kind = 'outbound_due'"),
    )
    op.drop_index(
        "uq_job_pending_outbound_sweep",
        table_name="job",
        postgresql_where=sa.text("status = 'pending' AND kind = 'outbound_sweep'"),
    )
    op.drop_constraint("ck_job_sweep_contact", "job", type_="check")
    op.drop_constraint("ck_job_kind", "job", type_="check")
    op.create_check_constraint(
        "ck_job_kind",
        "job",
        "kind IN ('reminder_due', 'integration_notify', 'automation_due')",
    )
    op.alter_column("job", "contact_id", existing_type=sa.BigInteger(), nullable=False)


def downgrade() -> None:
    op.alter_column("job", "contact_id", existing_type=sa.BigInteger(), nullable=True)
    op.drop_constraint("ck_job_kind", "job", type_="check")
    op.create_check_constraint(
        "ck_job_kind",
        "job",
        "kind IN ('reminder_due', 'integration_notify', 'outbound_sweep', "
        "'outbound_due', 'automation_due')",
    )
    op.create_check_constraint(
        "ck_job_sweep_contact",
        "job",
        "(kind = 'outbound_sweep' AND contact_id IS NULL) OR "
        "(kind <> 'outbound_sweep' AND contact_id IS NOT NULL)",
    )
    op.create_index(
        "uq_job_pending_outbound_sweep",
        "job",
        ["kind"],
        unique=True,
        postgresql_where=sa.text("status = 'pending' AND kind = 'outbound_sweep'"),
    )
    op.create_index(
        "uq_job_pending_outbound_due_per_contact",
        "job",
        ["contact_id"],
        unique=True,
        postgresql_where=sa.text("status = 'pending' AND kind = 'outbound_due'"),
    )

    op.create_table(
        "briefing_state",
        sa.Column("contact_id", sa.BigInteger(), nullable=False),
        sa.Column("opted_out_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("pending_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "unanswered_knocks",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "cadence",
            sa.Text(),
            server_default=sa.text("'daily'"),
            nullable=False,
        ),
        sa.Column("last_knock_on", sa.Date(), nullable=True),
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
            "cadence IN ('daily', 'weekly')",
            name="ck_briefing_state_cadence",
        ),
        sa.CheckConstraint(
            "unanswered_knocks >= 0",
            name="ck_briefing_state_unanswered",
        ),
        sa.ForeignKeyConstraint(
            ["contact_id"],
            ["contact.id"],
            name="briefing_state_contact_id_fkey",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("contact_id"),
    )
    op.execute("ALTER TABLE briefing_state ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
            REVOKE ALL ON TABLE briefing_state FROM anon;
          END IF;
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
            REVOKE ALL ON TABLE briefing_state FROM authenticated;
          END IF;
        END $$;
        """
    )
