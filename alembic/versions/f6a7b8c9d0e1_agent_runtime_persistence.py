"""add agent runtime persistence primitives

Revision ID: f6a7b8c9d0e1
Revises: c4d9e0f1a2b3
Create Date: 2026-08-15 16:00:00.000000

Execution records are audit/dedupe state, never a durable conversation
queue. Interaction outbound reservations live on ``message`` so a visible
side effect is reserved before calling Twilio.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "f6a7b8c9d0e1"
down_revision: Union[str, Sequence[str], None] = "c4d9e0f1a2b3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


ACTIVE_RUN_STATUSES = "'pending', 'running', 'cancel_requested'"


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
    op.create_table(
        "execution_run",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("contact_id", sa.BigInteger(), nullable=False),
        sa.Column("goal", sa.Text(), nullable=False),
        sa.Column(
            "toolkit_scope",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("dedupe_key", sa.Text(), nullable=False),
        sa.Column(
            "status", sa.Text(), server_default=sa.text("'pending'"), nullable=False
        ),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "cancel_requested", sa.Boolean(), server_default=sa.false(), nullable=False
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
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
            "status IN ('pending', 'running', 'succeeded', 'failed', "
            "'timed_out', 'cancel_requested', 'cancelled', 'abandoned')",
            name="ck_execution_run_status",
        ),
        sa.ForeignKeyConstraint(
            ["contact_id"],
            ["contact.id"],
            name="execution_run_contact_id_fkey",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_execution_run_active_contact_dedupe",
        "execution_run",
        ["contact_id", "dedupe_key"],
        unique=True,
        postgresql_where=sa.text(f"status IN ({ACTIVE_RUN_STATUSES})"),
    )
    op.create_index(
        "ix_execution_run_active_contact",
        "execution_run",
        ["contact_id", "created_at"],
        postgresql_where=sa.text(f"status IN ({ACTIVE_RUN_STATUSES})"),
    )
    _revoke_data_api("execution_run")

    op.create_table(
        "execution_event",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("contact_id", sa.BigInteger(), nullable=False),
        sa.Column("execution_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["contact_id"],
            ["contact.id"],
            name="execution_event_contact_id_fkey",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["execution_run_id"],
            ["execution_run.id"],
            name="execution_event_execution_run_id_fkey",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_execution_event_contact_unprocessed",
        "execution_event",
        ["contact_id", "created_at"],
        postgresql_where=sa.text("processed_at IS NULL"),
    )
    op.create_index(
        "ix_execution_event_run_created",
        "execution_event",
        ["execution_run_id", "created_at"],
    )
    _revoke_data_api("execution_event")

    op.add_column(
        "message",
        sa.Column("interaction_run_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column("message", sa.Column("outbound_sequence", sa.Integer(), nullable=True))
    op.add_column("message", sa.Column("delivery_state", sa.Text(), nullable=True))
    op.create_check_constraint(
        "ck_message_delivery_state",
        "message",
        "delivery_state IS NULL OR delivery_state IN ('reserved', 'sent', 'failed')",
    )
    op.create_index(
        "uq_message_interaction_outbound_sequence",
        "message",
        ["contact_id", "interaction_run_id", "outbound_sequence"],
        unique=True,
        postgresql_where=sa.text(
            "direction = 'out' AND interaction_run_id IS NOT NULL "
            "AND outbound_sequence IS NOT NULL"
        ),
    )

    op.add_column("pending_action", sa.Column("payload_hash", sa.Text(), nullable=True))
    # jsonb text is canonicalized by Postgres, so this is stable across key order.
    op.execute(
        "UPDATE pending_action "
        "SET payload_hash = encode(digest(payload::text, 'sha256'), 'hex') "
        "WHERE payload_hash IS NULL"
    )
    op.alter_column("pending_action", "payload_hash", nullable=False)
    op.add_column(
        "pending_action",
        sa.Column("source_interaction_run_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "pending_action",
        sa.Column("source_execution_run_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "pending_action_source_execution_run_id_fkey",
        "pending_action",
        "execution_run",
        ["source_execution_run_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.drop_constraint("ck_pending_action_status", "pending_action", type_="check")
    op.create_check_constraint(
        "ck_pending_action_status",
        "pending_action",
        "status IN ('pending', 'claimed', 'executed', 'cancelled', 'expired', 'failed')",
    )


def downgrade() -> None:
    # Old releases only know pending/claimed. Preserve safety during rollback:
    # terminal rows become claimed rather than accidentally confirmable again.
    op.execute(
        "UPDATE pending_action SET status = 'claimed' "
        "WHERE status NOT IN ('pending', 'claimed')"
    )
    op.drop_constraint("ck_pending_action_status", "pending_action", type_="check")
    op.create_check_constraint(
        "ck_pending_action_status",
        "pending_action",
        "status IN ('pending', 'claimed')",
    )
    op.drop_constraint(
        "pending_action_source_execution_run_id_fkey",
        "pending_action",
        type_="foreignkey",
    )
    op.drop_column("pending_action", "source_execution_run_id")
    op.drop_column("pending_action", "source_interaction_run_id")
    op.drop_column("pending_action", "payload_hash")

    op.drop_index("uq_message_interaction_outbound_sequence", table_name="message")
    op.drop_constraint("ck_message_delivery_state", "message", type_="check")
    op.drop_column("message", "delivery_state")
    op.drop_column("message", "outbound_sequence")
    op.drop_column("message", "interaction_run_id")

    _restore_data_api("execution_event")
    op.drop_index("ix_execution_event_run_created", table_name="execution_event")
    op.drop_index("ix_execution_event_contact_unprocessed", table_name="execution_event")
    op.drop_table("execution_event")

    _restore_data_api("execution_run")
    op.drop_index("ix_execution_run_active_contact", table_name="execution_run")
    op.drop_index("uq_execution_run_active_contact_dedupe", table_name="execution_run")
    op.drop_table("execution_run")
