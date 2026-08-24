from contextlib import asynccontextmanager

import logfire
import uvicorn
from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from app.api.dispatch import dispatch_inbound
from app.connect.pages import error_page, landing_page, mask_phone, success_page
from app.connect.token import (
    CONSENT_MAX_AGE_SECONDS,
    sign_connect_token,
    unsign_connect_token,
)
from app.database.models import Contact
from app.db import (
    claim_connect_link,
    enqueue_integration_notify,
    get_session,
    get_usable_connect_link,
    insert_inbound_message,
    start_connect_link,
    upsert_contact,
    upsert_integration,
)
from app.integrations import composio as composio_mod
from app.integrations.providers import UnknownProvider, get_provider
from app.core.config import settings
from app.core.logutil import get_logger, setup_logging
from app.core.observability import configure_observability, instrument_fastapi_app
from app.services.execution import abandon_expired_executions
from app.transport.twilio_wa import InboundMessage
from app.transport.twilio_wa import parse_inbound as parse_twilio_inbound
from app.transport.twilio_wa import validate_signature as validate_twilio_signature

setup_logging()
configure_observability(service_name="wpp-agent-api")
log = get_logger(__name__)

# On API startup: mark any orphaned in-process executions as abandoned.
@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Discard orphaned in-process executions after an API restart."""
    try:
        count = await abandon_expired_executions()
        if count:
            log.info(
                "execution_runs_abandoned_on_startup",
                extra={"event": "execution_runs_abandoned_on_startup", "count": count},
            )
    except Exception:
        log.exception(
            "execution_abandonment_cleanup_failed",
            extra={"event": "execution_abandonment_cleanup_failed"},
        )
    yield


app = FastAPI(lifespan=lifespan)
instrument_fastapi_app(app)

_LINK_ERROR = (
    "Esse link é inválido, já foi usado ou expirou. "
    "Pede um link novo no WhatsApp."
)
_PROVIDER_ERROR = (
    "Esse provedor não está disponível. "
    "Pede um link novo no WhatsApp com um serviço suportado."
)
_COMPOSIO_ERROR = (
    "Não deu pra confirmar a conexão. "
    "Tenta de novo com um link novo no WhatsApp."
)


# Look up provider metadata or return None if the slug isn't supported.
def _resolve_provider(provider: str):
    """Return registry Provider or None when the slug is unknown."""
    try:
        return get_provider(provider)
    except UnknownProvider:
        return None


@app.get("/health")
# Simple liveness check for Railway / load balancers.
def health():
    return {"status": "ok"}


@app.get("/connect/{provider}", response_class=HTMLResponse)
# Show the "connect Gmail" landing page after the user opens the signed link.
async def connect_landing(provider: str, t: str | None = None):
    """Composio connect landing: validate signed link + provider match."""
    meta = _resolve_provider(provider)
    if meta is None:
        return HTMLResponse(error_page(message=_PROVIDER_ERROR), status_code=400)

    slug = meta.slug
    if not t:
        return HTMLResponse(error_page(message=_LINK_ERROR), status_code=400)

    nonce = unsign_connect_token(t)
    if nonce is None:
        return HTMLResponse(error_page(message=_LINK_ERROR), status_code=400)

    async with get_session() as session:
        link = await get_usable_connect_link(session, nonce=nonce)
        if link is None or link.provider != slug:
            await session.commit()
            return HTMLResponse(error_page(message=_LINK_ERROR), status_code=400)
        contact = await session.get(Contact, link.contact_id)
        await session.commit()

    if contact is None:
        return HTMLResponse(error_page(message=_LINK_ERROR), status_code=400)

    start_url = f"{settings.app_base_url}/connect/{slug}/start?t={t}"
    return HTMLResponse(
        landing_page(
            masked_phone=mask_phone(contact.phone),
            start_url=start_url,
            title=meta.landing_title_pt,
            body_pt=meta.landing_body_pt,
            cta=meta.connect_cta_pt,
        )
    )


@app.get("/connect/{provider}/start")
# Redirect the user to Composio/Google OAuth to grant access.
async def connect_start(provider: str, t: str | None = None):
    """Start Composio managed-auth; 302 to the authorize redirect URL."""
    meta = _resolve_provider(provider)
    if meta is None:
        return HTMLResponse(error_page(message=_PROVIDER_ERROR), status_code=400)

    slug = meta.slug
    if not t:
        return HTMLResponse(error_page(message=_LINK_ERROR), status_code=400)

    nonce = unsign_connect_token(t)
    if nonce is None:
        return HTMLResponse(error_page(message=_LINK_ERROR), status_code=400)

    async with get_session() as session:
        link = await start_connect_link(session, nonce=nonce)
        if link is None or link.provider != slug:
            await session.commit()
            return HTMLResponse(error_page(message=_LINK_ERROR), status_code=400)
        contact_id = link.contact_id
        await session.commit()

    # Fresh signature for the success callback: contact binding stays on our
    # token, not anything Composio appends.
    callback_url = (
        f"{settings.app_base_url}/connect/{slug}/success"
        f"?t={sign_connect_token(nonce)}"
    )
    try:
        connection = composio_mod.authorize(
            contact_id,
            slug,
            callback_url=callback_url,
        )
    except Exception:
        log.exception(
            "composio_authorize_failed",
            extra={
                "event": "composio_authorize_failed",
                "contact_id": contact_id,
                "provider": slug,
            },
        )
        return HTMLResponse(error_page(message=_COMPOSIO_ERROR), status_code=400)

    redirect_url = getattr(connection, "redirect_url", None)
    if not isinstance(redirect_url, str) or not redirect_url:
        log.info(
            "composio_authorize_missing_redirect",
            extra={
                "event": "composio_authorize_missing_redirect",
                "contact_id": contact_id,
                "provider": slug,
            },
        )
        return HTMLResponse(error_page(message=_COMPOSIO_ERROR), status_code=400)

    return RedirectResponse(url=redirect_url, status_code=302)


@app.get("/connect/{provider}/success", response_class=HTMLResponse)
# OAuth callback: save the integration and queue a WhatsApp confirmation.
async def connect_success(provider: str, t: str | None = None):
    """Claim the link, upsert integration, notify — or serve the static page.

    With ``t``: one-time callback (atomic claim). Without ``t``: safe reload
    target after the 303 redirect.
    """
    meta = _resolve_provider(provider)
    if meta is None:
        return HTMLResponse(error_page(message=_PROVIDER_ERROR), status_code=400)

    slug = meta.slug
    if t is None:
        return HTMLResponse(success_page(display_name=meta.display_name))

    nonce = unsign_connect_token(t, max_age=CONSENT_MAX_AGE_SECONDS)
    if nonce is None:
        return HTMLResponse(error_page(message=_LINK_ERROR), status_code=400)

    async with get_session() as session:
        usable = await get_usable_connect_link(session, nonce=nonce)
        if usable is None or usable.provider != slug:
            await session.commit()
            return HTMLResponse(error_page(message=_LINK_ERROR), status_code=400)
        link = await claim_connect_link(session, nonce=nonce)
        if link is None:
            await session.commit()
            return HTMLResponse(error_page(message=_LINK_ERROR), status_code=400)
        contact_id = link.contact_id
        contact = await session.get(Contact, contact_id)
        phone = contact.phone if contact is not None else None
        await session.commit()

    # Composio list is a network call — keep it outside the DB session.
    try:
        external_account_id = composio_mod.find_active_connected_account_id(
            contact_id,
            slug,
        )
    except Exception:
        log.exception(
            "composio_verify_failed",
            extra={
                "event": "composio_verify_failed",
                "contact_id": contact_id,
                "provider": slug,
            },
        )
        return HTMLResponse(error_page(message=_COMPOSIO_ERROR), status_code=400)

    if not external_account_id:
        log.info(
            "composio_account_missing",
            extra={
                "event": "composio_account_missing",
                "contact_id": contact_id,
                "provider": slug,
            },
        )
        return HTMLResponse(error_page(message=_COMPOSIO_ERROR), status_code=400)

    notify_payload: dict[str, str] = {"provider": slug}
    if phone:
        notify_payload["phone"] = phone

    async with get_session() as session:
        await upsert_integration(
            session,
            contact_id=contact_id,
            provider=slug,
            external_account_id=external_account_id,
        )
        await enqueue_integration_notify(
            session,
            contact_id=contact_id,
            payload=notify_payload,
        )
        await session.commit()

    log.info(
        "composio_connected",
        extra={
            "event": "composio_connected",
            "contact_id": contact_id,
            "provider": slug,
        },
    )
    return RedirectResponse(
        url=f"{settings.app_base_url}/connect/{slug}/success",
        status_code=303,
    )


async def _ingest_inbound(msg: InboundMessage) -> dict[str, object]:
    """Persist inbound. Schedule after-200 dispatch when body is non-empty."""
    async with get_session() as session:
        contact = await upsert_contact(
            session, phone=msg.phone, name=msg.sender_name
        )
        inserted = await insert_inbound_message(
            session,
            contact_id=contact.id,
            body=msg.body,
            provider_message_id=msg.provider_message_id,
            account_sid=msg.account_sid,
            from_address=msg.from_address,
            to_address=msg.to_address,
            num_media=msg.num_media,
            media_url=msg.media_url,
            media_content_type=msg.media_content_type,
            wa_id=msg.wa_id,
            sms_status=msg.sms_status,
            api_version=msg.api_version,
            num_segments=msg.num_segments,
            profile_name=msg.profile_name,
        )
        await session.commit()

    if not inserted:
        with logfire.span(
            "duplicate_suppression",
            provider="twilio",
            provider_message_id=msg.provider_message_id,
        ):
            log.info(
                "duplicate_inbound_skipped",
                extra={
                    "event": "duplicate_inbound_skipped",
                    "provider_message_id": msg.provider_message_id,
                },
            )
        return {"ok": True, "dispatch": False}

    if not msg.body.strip():
        log.info(
            "inbound_persisted_no_dispatch",
            extra={
                "event": "inbound_persisted_no_dispatch",
                "contact_id": contact.id,
                "provider_message_id": msg.provider_message_id,
                "num_media": msg.num_media,
            },
        )
        return {"ok": True, "dispatch": False}

    return {
        "ok": True,
        "dispatch": True,
        "contact_id": contact.id,
        "phone": msg.phone,
        "provider_message_id": msg.provider_message_id,
    }


@app.post("/webhook/twilio")
# Twilio WhatsApp webhook: verify signature, save message, return 200 fast.
async def webhook_twilio(request: Request, background_tasks: BackgroundTasks):
    form = await request.form()
    params = {key: str(value) for key, value in form.items()}
    # Use the public origin — request.url may be http:// behind Cloudflare.
    public_url = f"{settings.app_base_url}/webhook/twilio"
    signature = request.headers.get("X-Twilio-Signature")
    if not validate_twilio_signature(
        url=public_url, params=params, signature=signature
    ):
        log.info(
            "twilio_signature_rejected",
            extra={"event": "twilio_signature_rejected"},
        )
        return Response(status_code=403)

    log.info(
        "webhook_received",
        extra={
            "event": "webhook_received",
            "provider": "twilio",
            "message_sid": params.get("MessageSid") or params.get("SmsSid"),
        },
    )

    msg = parse_twilio_inbound(params)
    if msg is not None:
        with logfire.span(
            "webhook",
            provider="twilio",
            provider_message_id=msg.provider_message_id,
        ):
            result = await _ingest_inbound(msg)
        if result.get("dispatch"):
            background_tasks.add_task(
                dispatch_inbound,
                contact_id=result["contact_id"],
                phone=result["phone"],
                provider_message_id=result["provider_message_id"],
            )

    # Acknowledge without TwiML — chat/agent work runs after this 200.
    return Response(content="<Response></Response>", media_type="text/xml")


if __name__ == "__main__":
    uvicorn.run("app.api.main:app", host="0.0.0.0", port=8000, reload=True)
