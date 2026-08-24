"""Contact upsert and lookup."""

from __future__ import annotations

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Contact


async def upsert_contact(
    session: AsyncSession,
    *,
    phone: str,
    name: str | None,
) -> Contact:
    """Insert or update a contact, always refreshing ``last_seen_at``."""
    values: dict[str, object] = {
        "phone": phone,
        "last_seen_at": func.now(),
    }
    if name is not None:
        values["name"] = name

    update_set: dict[str, object] = {"last_seen_at": func.now()}
    if name is not None:
        update_set["name"] = name

    stmt = (
        insert(Contact)
        .values(**values)
        .on_conflict_do_update(index_elements=[Contact.phone], set_=update_set)
        .returning(Contact)
    )
    return (
        await session.scalars(stmt, execution_options={"populate_existing": True})
    ).one()
