"""reshape integration + connect_link for Composio-only

Revision ID: b8c1d2e3f4a5
Revises: a7b8c9d0e1f2
Create Date: 2026-08-10 14:40:00.000000

Composio owns tokens, scopes, refresh, and revocation (ADR 0008).
No production rows to preserve — drop refresh_token_enc and scopes.

connect_link.provider backfill uses legacy ``google`` (DIY Google OAuth
covered Gmail+Calendar as one grant). New connects (023+) use Composio
toolkit slugs from the registry (gmail, googlecalendar, …). 023/025 must
map or rewrite any remaining ``google`` rows when DIY is removed.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b8c1d2e3f4a5"
down_revision: Union[str, Sequence[str], None] = "a7b8c9d0e1f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "integration",
        sa.Column("external_account_id", sa.Text(), nullable=True),
    )
    op.drop_column("integration", "refresh_token_enc")
    op.drop_column("integration", "scopes")

    op.add_column(
        "connect_link",
        sa.Column("provider", sa.Text(), nullable=True),
    )
    # Legacy DIY Google links (Gmail+Calendar bundled). Not a registry slug;
    # 023/025 map or rewrite. Prefer toolkit slugs for all new rows.
    op.execute("UPDATE connect_link SET provider = 'google' WHERE provider IS NULL")
    op.alter_column(
        "connect_link",
        "provider",
        existing_type=sa.Text(),
        nullable=False,
    )


def downgrade() -> None:
    op.drop_column("connect_link", "provider")

    op.add_column(
        "integration",
        sa.Column(
            "scopes",
            sa.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
    )
    op.add_column(
        "integration",
        sa.Column("refresh_token_enc", sa.Text(), nullable=True),
    )
    # Downgrade cannot restore dropped ciphertext; leave nullable.
    op.drop_column("integration", "external_account_id")
