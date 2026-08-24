"""add pending_action status and created_turn_id

Revision ID: e5f9c3d20b16
Revises: d4e7b2c81a05
Create Date: 2026-08-08 22:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "e5f9c3d20b16"
down_revision: Union[str, Sequence[str], None] = "d4e7b2c81a05"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 'claimed' = a confirm turn is executing this row. Only 'pending' rows are
    # confirmable, so the action survives a failed Google call (released back).
    op.add_column(
        "pending_action",
        sa.Column(
            "status",
            sa.Text(),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
    )
    # Job id of the turn that staged the row: confirming from the same turn is
    # refused, so a "yes" always has to arrive as a separate message.
    op.add_column(
        "pending_action",
        sa.Column("created_turn_id", sa.Text(), nullable=True),
    )
    op.create_check_constraint(
        "ck_pending_action_status",
        "pending_action",
        "status IN ('pending', 'claimed')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_pending_action_status", "pending_action", type_="check"
    )
    op.drop_column("pending_action", "created_turn_id")
    op.drop_column("pending_action", "status")
