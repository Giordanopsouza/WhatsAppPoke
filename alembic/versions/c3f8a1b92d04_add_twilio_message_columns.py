"""add typed Twilio columns on message

Revision ID: c3f8a1b92d04
Revises: b2e9f1a04c83
Create Date: 2026-08-08 19:55:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c3f8a1b92d04"
down_revision: Union[str, Sequence[str], None] = "b2e9f1a04c83"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("message", sa.Column("account_sid", sa.Text(), nullable=True))
    op.add_column("message", sa.Column("from_address", sa.Text(), nullable=True))
    op.add_column("message", sa.Column("to_address", sa.Text(), nullable=True))
    op.add_column("message", sa.Column("num_media", sa.Integer(), nullable=True))
    op.add_column("message", sa.Column("media_url", sa.Text(), nullable=True))
    op.add_column(
        "message", sa.Column("media_content_type", sa.Text(), nullable=True)
    )
    op.add_column("message", sa.Column("wa_id", sa.Text(), nullable=True))
    op.add_column("message", sa.Column("sms_status", sa.Text(), nullable=True))
    op.add_column("message", sa.Column("api_version", sa.Text(), nullable=True))
    op.add_column("message", sa.Column("num_segments", sa.Integer(), nullable=True))
    op.add_column("message", sa.Column("profile_name", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("message", "profile_name")
    op.drop_column("message", "num_segments")
    op.drop_column("message", "api_version")
    op.drop_column("message", "sms_status")
    op.drop_column("message", "wa_id")
    op.drop_column("message", "media_content_type")
    op.drop_column("message", "media_url")
    op.drop_column("message", "num_media")
    op.drop_column("message", "to_address")
    op.drop_column("message", "from_address")
    op.drop_column("message", "account_sid")
