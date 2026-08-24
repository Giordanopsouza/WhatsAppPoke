"""add contact.tz

Revision ID: b2e9f1a04c83
Revises: a1c8e4f92b07
Create Date: 2026-08-07 20:05:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b2e9f1a04c83"
down_revision: Union[str, Sequence[str], None] = "a1c8e4f92b07"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

DEFAULT_TZ = "America/Sao_Paulo"


def upgrade() -> None:
    op.add_column(
        "contact",
        sa.Column(
            "tz",
            sa.Text(),
            nullable=False,
            server_default=sa.text(f"'{DEFAULT_TZ}'"),
        ),
    )


def downgrade() -> None:
    op.drop_column("contact", "tz")
