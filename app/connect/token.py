"""Signed one-time connect tokens (itsdangerous) for the connect-link flow."""

from __future__ import annotations

from itsdangerous import BadData, URLSafeTimedSerializer

from app.core.config import settings

# Salt kept for in-flight DIY Google links until task 025 removes that path.
CONNECT_SALT = "google-connect"
# WhatsApp link → landing page → "Conectar" button.
CONNECT_MAX_AGE_SECONDS = 10 * 60
# "Conectar" → provider consent → success callback. Longer budget so a slow
# consent screen (or Composio interstitial) does not burn a granted auth.
CONSENT_MAX_AGE_SECONDS = 30 * 60


# Build the signer used to create and verify connect-link tokens.
def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(
        settings.connect_signing_key,
        salt=CONNECT_SALT,
    )


# Turn a connect-link nonce into a signed token for the URL.
def sign_connect_token(nonce: str) -> str:
    return _serializer().dumps(nonce)


def unsign_connect_token(
    token: str,
    *,
    max_age: int = CONNECT_MAX_AGE_SECONDS,
) -> str | None:
    """Return the nonce, or None if the signature is bad/expired.

    ``BadData`` is the common base of every itsdangerous failure (bad
    signature, expired, undecodable payload) — a malformed token must render
    the friendly error page, never a 500.
    """
    try:
        nonce = _serializer().loads(token, max_age=max_age)
    except BadData:
        return None
    if not isinstance(nonce, str) or not nonce:
        return None
    return nonce
