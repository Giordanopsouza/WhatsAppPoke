"""retire legacy queued conversation runtime

Revision ID: c5d6e7f8a9b0
Revises: b3c4d5e6f7a8
Create Date: 2026-08-16 01:30:00.000000

Interaction is the only inbound conversation runtime. Existing ``agent_turn``
rows cannot be processed after its handler is removed, so this migration
discards them before removing their job kind and pending-row index. Durable
background job kinds are unchanged.
"""

from typing import Sequence, Union

from alembic import op


revision: str = "c5d6e7f8a9b0"
down_revision: Union[str, Sequence[str], None] = "b3c4d5e6f7a8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("DELETE FROM job WHERE kind = 'agent_turn'")
    op.drop_index("uq_job_pending_agent_turn_per_contact", table_name="job")
    op.drop_constraint("ck_job_kind", "job", type_="check")
    op.create_check_constraint(
        "ck_job_kind",
        "job",
        "kind IN ('reminder_due', 'integration_notify', 'outbound_sweep', "
        "'outbound_due', 'automation_due')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_job_kind", "job", type_="check")
    op.create_check_constraint(
        "ck_job_kind",
        "job",
        "kind IN ('agent_turn', 'reminder_due', 'integration_notify', "
        "'outbound_sweep', 'outbound_due', 'automation_due')",
    )
    op.create_index(
        "uq_job_pending_agent_turn_per_contact",
        "job",
        ["contact_id"],
        unique=True,
        postgresql_where="status = 'pending' AND kind = 'agent_turn'",
    )
