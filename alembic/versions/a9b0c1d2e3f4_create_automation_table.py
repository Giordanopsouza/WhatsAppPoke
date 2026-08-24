"""create automation table and automation_due job kind

Revision ID: a9b0c1d2e3f4
Revises: f6a7b8c9d0e1
Create Date: 2026-08-15 22:00:00.000000

Automation is a contact-scoped RRULE goal. The worker wakes it through
``automation_due``; Execution never sends WhatsApp itself.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "a9b0c1d2e3f4"
down_revision: Union[str, Sequence[str], None] = "f6a7b8c9d0e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _revoke_data_api(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(
        f"""
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
            REVOKE ALL ON TABLE {table} FROM anon;
          END IF;
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
            REVOKE ALL ON TABLE {table} FROM authenticated;
          END IF;
        END $$;
        """
    )


def _restore_data_api(table: str) -> None:
    op.execute(
        f"""
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
            GRANT ALL ON TABLE {table} TO anon;
          END IF;
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
            GRANT ALL ON TABLE {table} TO authenticated;
          END IF;
        END $$;
        """
    )
    op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")


def upgrade() -> None:
    op.drop_constraint("ck_job_kind", "job", type_="check")
    op.create_check_constraint(
        "ck_job_kind",
        "job",
        "kind IN ('agent_turn', 'reminder_due', 'integration_notify', "
        "'outbound_sweep', 'outbound_due', 'automation_due')",
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_job_pending_automation_due
        ON job ((payload->>'automation_id'))
        WHERE status = 'pending' AND kind = 'automation_due'
        """
    )

    op.create_table(
        "automation",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("contact_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("goal", sa.Text(), nullable=False),
        sa.Column("rrule", sa.Text(), nullable=False),
        sa.Column("timezone", sa.Text(), nullable=False),
        sa.Column(
            "required_toolkits",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "status", sa.Text(), server_default=sa.text("'active'"), nullable=False
        ),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_run_status", sa.Text(), nullable=True),
        sa.Column("last_occurrence_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_execution_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "last_run_was_catch_up",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
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
            "status IN ('active', 'paused', 'cancelled')",
            name="ck_automation_status",
        ),
        sa.CheckConstraint(
            "last_run_status IS NULL OR last_run_status IN "
            "('succeeded', 'failed', 'timed_out', 'cancelled', 'skipped')",
            name="ck_automation_last_run_status",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(required_toolkits) = 'array'",
            name="ck_automation_required_toolkits_array",
        ),
        sa.ForeignKeyConstraint(
            ["contact_id"],
            ["contact.id"],
            name="automation_contact_id_fkey",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_automation_contact_status",
        "automation",
        ["contact_id", "status"],
    )
    op.create_index(
        "ix_automation_active_next_run",
        "automation",
        ["next_run_at"],
        postgresql_where=sa.text("status = 'active' AND next_run_at IS NOT NULL"),
    )
    _revoke_data_api("automation")


def downgrade() -> None:
    _restore_data_api("automation")
    op.drop_index(
        "ix_automation_active_next_run",
        table_name="automation",
        postgresql_where=sa.text("status = 'active' AND next_run_at IS NOT NULL"),
    )
    op.drop_index("ix_automation_contact_status", table_name="automation")
    op.drop_table("automation")

    op.execute("DROP INDEX IF EXISTS uq_job_pending_automation_due")
    op.execute("DELETE FROM job WHERE kind = 'automation_due'")
    op.drop_constraint("ck_job_kind", "job", type_="check")
    op.create_check_constraint(
        "ck_job_kind",
        "job",
        "kind IN ('agent_turn', 'reminder_due', 'integration_notify', "
        "'outbound_sweep', 'outbound_due')",
    )
