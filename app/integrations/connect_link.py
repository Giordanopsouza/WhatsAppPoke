"""Contact-scoped managed-auth link minting, independent of any agent runtime."""

import secrets

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.connect.token import sign_connect_token
from app.core.config import settings
from app.db import create_connect_link
from app.integrations.providers import PROVIDERS, UnknownProvider, get_provider


# Create and return a signed OAuth connect URL for the agent to send.
async def mint_connect_link(
    *,
    contact_id: int,
    session_factory: async_sessionmaker[AsyncSession],
    provider: str,
) -> str:
    """Persist and return a one-time connect link for exactly one contact."""
    try:
        meta = get_provider(provider)
    except UnknownProvider:
        allowed = ", ".join(sorted(PROVIDERS))
        return f'Provider "{provider}" is not supported. Allowed: {allowed}.'

    nonce = secrets.token_urlsafe(32)
    async with session_factory() as session:
        await create_connect_link(
            session,
            contact_id=contact_id,
            nonce=nonce,
            provider=meta.slug,
        )
        await session.commit()

    token = sign_connect_token(nonce)
    url = f"{settings.app_base_url}/connect/{meta.slug}?t={token}"
    return (
        f"Connect link (expires in 10 minutes): {url}\n"
        f"Tell the person to open it, tap {meta.connect_cta_pt}, and approve "
        f"{meta.display_name} access. They will get a WhatsApp confirmation "
        "when it works."
    )
