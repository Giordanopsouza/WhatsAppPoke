"""Automation due job handler."""

from __future__ import annotations

from app.database.models import Job
from app.services.automation import fire_due_automation


async def handle_automation_due(job: Job) -> None:
    """Wake one due automation through Execution; Interaction is the speaker."""
    await fire_due_automation(job)
