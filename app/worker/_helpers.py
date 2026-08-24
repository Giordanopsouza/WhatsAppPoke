"""Shared helpers for job handlers."""

from __future__ import annotations

from typing import Any

from app.database.models import Job
from app.core.logutil import get_logger

log = get_logger(__name__)


# Read a string field from a job's JSON payload safely.
def payload_str(payload: dict[str, Any], key: str) -> str | None:
    value = payload.get(key)
    return value if isinstance(value, str) and value else None


# Get the contact_id from a job (raises if the job has no contact).
def contact_id(job: Job) -> int:
    if job.contact_id is None:
        raise RuntimeError(f"{job.kind} job {job.id} missing contact_id")
    return job.contact_id
