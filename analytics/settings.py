"""Config for the analytics dashboard.

Deliberately separate from ``app.core.config``: that Settings validates every var
the agent runtime needs (Twilio, Composio, OpenRouter…) at import time, so reusing
it here would crash-loop a service that only reads the database.
"""

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def digits_phone(value: str) -> str:
    """Strip ``+`` / spaces / punctuation → digits-only, matching ``contact.phone``."""
    return "".join(ch for ch in value if ch.isdigit())


def normalize_excluded_phones(raw: str) -> list[str]:
    """Parse ``ANALYTICS_EXCLUDE_PHONES``. ``+5511…`` and ``5511…`` both match."""
    return [digits_phone(p) for p in raw.split(",") if digits_phone(p)]


class AnalyticsSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Same URL the app uses (asyncpg scheme); converted to a sync driver below.
    database_url: str = Field(validation_alias="DATABASE_URL")
    analytics_user: str = Field(validation_alias="ANALYTICS_USER")
    analytics_password: str = Field(validation_alias="ANALYTICS_PASSWORD")
    # Comma-separated phones to hide (your own test numbers). ``+E.164`` or
    # digits-only both work — stored ``contact.phone`` is digits-only.
    # Empty means show everyone.
    analytics_exclude_phones: str = Field(
        default="", validation_alias="ANALYTICS_EXCLUDE_PHONES"
    )
    analytics_tz: str = Field(
        default="America/Sao_Paulo", validation_alias="ANALYTICS_TZ"
    )

    @field_validator("analytics_password")
    @classmethod
    def password_must_be_strong_enough(cls, v: str) -> str:
        if len(v) < 12:
            raise ValueError("ANALYTICS_PASSWORD must be at least 12 characters")
        return v

    @property
    def sync_database_url(self) -> str:
        """psycopg (sync) URL — Streamlit runs synchronously."""
        return self.database_url.replace(
            "postgresql+asyncpg://", "postgresql+psycopg://", 1
        )

    @property
    def excluded_phones(self) -> list[str]:
        return normalize_excluded_phones(self.analytics_exclude_phones)
