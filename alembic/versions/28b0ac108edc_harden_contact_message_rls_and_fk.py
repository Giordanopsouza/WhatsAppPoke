"""harden contact/message RLS and FK

Revision ID: 28b0ac108edc
Revises: 74a46d28dbc8
Create Date: 2026-08-05 10:37:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "28b0ac108edc"
down_revision: Union[str, Sequence[str], None] = "74a46d28dbc8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Explicit RESTRICT (Postgres default) so contacts with history cannot vanish.
    op.drop_constraint("message_contact_id_fkey", "message", type_="foreignkey")
    op.create_foreign_key(
        "message_contact_id_fkey",
        "message",
        "contact",
        ["contact_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    # Lock down PostgREST Data API: RLS on, no policies for anon/authenticated.
    # FastAPI uses the privileged pooler role and continues to bypass RLS.
    op.execute("ALTER TABLE contact ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE message ENABLE ROW LEVEL SECURITY")

    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
            REVOKE ALL ON TABLE contact FROM anon;
            REVOKE ALL ON TABLE message FROM anon;
          END IF;
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
            REVOKE ALL ON TABLE contact FROM authenticated;
            REVOKE ALL ON TABLE message FROM authenticated;
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
            GRANT ALL ON TABLE contact TO anon;
            GRANT ALL ON TABLE message TO anon;
          END IF;
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
            GRANT ALL ON TABLE contact TO authenticated;
            GRANT ALL ON TABLE message TO authenticated;
          END IF;
        END $$;
        """
    )

    op.execute("ALTER TABLE message DISABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE contact DISABLE ROW LEVEL SECURITY")

    op.drop_constraint("message_contact_id_fkey", "message", type_="foreignkey")
    op.create_foreign_key(
        "message_contact_id_fkey",
        "message",
        "contact",
        ["contact_id"],
        ["id"],
    )
