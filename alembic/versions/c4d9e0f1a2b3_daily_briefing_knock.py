"""daily briefing knock: briefing_state + outbound job kinds

Revision ID: c4d9e0f1a2b3
Revises: b8c1d2e3f4a5
Create Date: 2026-08-13 15:20:00.000000

``outbound_sweep`` is the only job kind with ``contact_id IS NULL``.
The worker seeds at most one pending sweep (unique index) and fans out
per-contact ``outbound_due`` jobs. Next sweep is ~15 minutes later
(not a São Paulo 07:40 cron) so contacts in other ``tz`` still hit
local 08:00.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c4d9e0f1a2b3"
down_revision: Union[str, Sequence[str], None] = "b8c1d2e3f4a5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint("ck_job_kind", "job", type_="check")
    op.create_check_constraint(
        "ck_job_kind",
        "job",
        "kind IN ('agent_turn', 'reminder_due', 'integration_notify', "
        "'outbound_sweep', 'outbound_due')",
    )

    op.alter_column("job", "contact_id", existing_type=sa.BigInteger(), nullable=True)
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


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
            GRANT ALL ON TABLE briefing_state TO anon;
          END IF;
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
            GRANT ALL ON TABLE briefing_state TO authenticated;
          END IF;
        END $$;
        """
    )
    op.execute("ALTER TABLE briefing_state DISABLE ROW LEVEL SECURITY")
    op.drop_table("briefing_state")

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
    op.alter_column("job", "contact_id", existing_type=sa.BigInteger(), nullable=False)

    op.drop_constraint("ck_job_kind", "job", type_="check")
    op.create_check_constraint(
        "ck_job_kind",
        "job",
        "kind IN ('agent_turn', 'reminder_due', 'integration_notify')",
    )
