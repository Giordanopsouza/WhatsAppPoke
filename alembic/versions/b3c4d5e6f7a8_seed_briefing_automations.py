"""seed briefing automations for existing contacts

Revision ID: b3c4d5e6f7a8
Revises: a9b0c1d2e3f4
Create Date: 2026-08-16 00:00:00.000000

Task 046: Migrate daily briefing onto Automation while preserving briefing_state
preferences (opt-out, weekly/daily cadence, and last knock).
"""

from datetime import datetime, timezone
from typing import Sequence, Union

from alembic import op
from sqlalchemy import text

from app.core.rrule import next_occurrence_utc


revision: str = "b3c4d5e6f7a8"
down_revision: Union[str, Sequence[str], None] = "a9b0c1d2e3f4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_BRIEFING_NAME = "Briefing Matinal"


def upgrade() -> None:
    # Seed one briefing Automation for every contact that does not have one yet.
    # Preserves opt_out (sets status to 'paused') and weekly cadence (rrule BYDAY=MO).
    # next_run_at is backfilled below for active rows so they match
    # ensure_briefing_automation. automation_due jobs are not inserted: outbound_sweep
    # still owns knocks until task 047.
    op.execute(
        """
        INSERT INTO automation (
            contact_id,
            name,
            goal,
            rrule,
            timezone,
            required_toolkits,
            status,
            next_run_at,
            created_at,
            updated_at
        )
        SELECT
            c.id AS contact_id,
            'Briefing Matinal' AS name,
            'Preparar e apresentar o resumo matinal com agenda e emails importantes do dia.' AS goal,
            CASE
                WHEN bs.cadence = 'weekly' THEN 'FREQ=WEEKLY;BYDAY=MO;BYHOUR=8;BYMINUTE=0;BYSECOND=0'
                ELSE 'FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR;BYHOUR=8;BYMINUTE=0;BYSECOND=0'
            END AS rrule,
            COALESCE(c.tz, 'America/Sao_Paulo') AS timezone,
            '["gmail", "googlecalendar"]'::jsonb AS required_toolkits,
            CASE
                WHEN bs.opted_out_at IS NOT NULL THEN 'paused'
                ELSE 'active'
            END AS status,
            NULL AS next_run_at,
            NOW() AS created_at,
            NOW() AS updated_at
        FROM contact c
        LEFT JOIN briefing_state bs ON bs.contact_id = c.id
        WHERE NOT EXISTS (
            SELECT 1 FROM automation a
            WHERE a.contact_id = c.id
              AND a.name = 'Briefing Matinal'
              AND a.status != 'cancelled'
        );
        """
    )

    conn = op.get_bind()
    now = datetime.now(timezone.utc)
    rows = conn.execute(
        text(
            """
            SELECT id, rrule, timezone
            FROM automation
            WHERE name = :name
              AND status = 'active'
              AND next_run_at IS NULL
            """
        ),
        {"name": _BRIEFING_NAME},
    ).mappings().all()
    for row in rows:
        conn.execute(
            text("UPDATE automation SET next_run_at = :next_run_at WHERE id = :id"),
            {
                "id": row["id"],
                "next_run_at": next_occurrence_utc(
                    rrule=row["rrule"],
                    timezone_name=row["timezone"],
                    after=now,
                    dtstart=now,
                ),
            },
        )


def downgrade() -> None:
    # Irreversible. Application code (ensure_briefing_automation) also creates
    # 'Briefing Matinal' rows after this revision; deleting by name would drop those.
    pass
