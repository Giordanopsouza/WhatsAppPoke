"""Engine, sessions, and shared DB constants."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings

HISTORY_LIMIT = 20
HISTORY_MAX_CHARS = 8000
STALE_LOCK_MINUTES = 10
BACKOFF_CAP_SECONDS = 300
CONNECT_LINK_TTL = timedelta(minutes=10)
CONNECT_CONSENT_TTL = timedelta(minutes=30)
PENDING_ACTION_TTL = timedelta(minutes=15)
INTERACTION_LOCK_NS = 870_315

engine = create_async_engine(
    settings.database_url,
    connect_args={"statement_cache_size": 0},
    # Supabase/PgBouncer closes idle clients; don't reuse dead sockets.
    pool_pre_ping=True,
    pool_recycle=280,
)

SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


@asynccontextmanager
# Open a short-lived database session (use with `async with`).
async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session


def backoff_seconds(attempts: int) -> int:
    """Exponential backoff after a failed attempt: 2, 4, 8, … capped."""
    return min(2**attempts, BACKOFF_CAP_SECONDS)


@asynccontextmanager
async def _contact_advisory_lock(ns: int, contact_id: int) -> AsyncIterator[None]:
    """Hold one transaction-scoped contact lock through a PgBouncer pooler."""
    async with engine.connect() as conn:
        await conn.execute(
            text("SELECT pg_advisory_xact_lock(:ns, :cid)"),
            {"ns": ns, "cid": contact_id},
        )
        try:
            yield
            await conn.commit()
        except BaseException:
            await conn.rollback()
            raise


@asynccontextmanager
async def contact_interaction_lock(contact_id: int) -> AsyncIterator[None]:
    """Serialize one Interaction event for a contact.

    This deliberately holds a transaction-scoped advisory lock for the one
    model run. PgBouncer transaction pooling keeps the lock attached to this
    dedicated connection until commit/rollback; other contacts use a distinct
    key and continue in parallel.
    """
    async with _contact_advisory_lock(INTERACTION_LOCK_NS, contact_id):
        yield
