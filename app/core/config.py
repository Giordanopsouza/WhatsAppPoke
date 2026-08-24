from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


# All app settings loaded from environment variables (.env).
class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",  # ignore SUPABASE_* / removed FERNET_KEY / GOOGLE_CLIENT_*
    )

    database_url: str = Field(validation_alias="DATABASE_URL")
    openrouter_api_key: str = Field(validation_alias="OPENROUTER_API_KEY")
    openrouter_chat_model: str = Field(
        validation_alias="OPENROUTER_CHAT_MODEL",
    )
    openrouter_exec_model: str = Field(
        validation_alias="OPENROUTER_EXEC_MODEL",
    )
    twilio_account_sid: str = Field(validation_alias="TWILIO_ACCOUNT_SID")
    twilio_auth_token: str = Field(validation_alias="TWILIO_AUTH_TOKEN")
    # WhatsApp sender address, e.g. whatsapp:+14155238886 (sandbox) or
    # whatsapp:+1… / bare +E.164 for a production WhatsApp-enabled number.
    twilio_whatsapp_from: str = Field(validation_alias="TWILIO_WHATSAPP_FROM")
    # Approved WhatsApp Utility Content Template for reminder pings outside
    # the 24h customer-service window. Template body must expose {{1}} for
    # the stored reminder body.
    twilio_reminder_content_sid: str = Field(
        validation_alias="TWILIO_REMINDER_CONTENT_SID"
    )
    # Approved WhatsApp Utility Content Template for proactive automation
    # ready notifications and action-needs-response outside the 24h customer
    # service window. Exposes {{1}} for the automation summary or prompt.
    twilio_automation_content_sid: str = Field(
        default="", validation_alias="TWILIO_AUTOMATION_CONTENT_SID"
    )
    twilio_action_content_sid: str = Field(
        default="", validation_alias="TWILIO_ACTION_CONTENT_SID"
    )
    tavily_api_key: str = Field(validation_alias="TAVILY_API_KEY")
    logfire_token: str = Field(validation_alias="LOGFIRE_TOKEN")
    sentry_dsn: str = Field(validation_alias="SENTRY_DSN")
    connect_signing_key: str = Field(validation_alias="CONNECT_SIGNING_KEY")
    app_base_url: str = Field(validation_alias="APP_BASE_URL")
    # Composio managed-auth and authenticated proxy (ADR 0015).
    composio_api_key: str = Field(validation_alias="COMPOSIO_API_KEY")
    # Detached Execution is best-effort api work, never a durable job.
    execution_timeout_seconds: int = Field(
        default=90, validation_alias="EXECUTION_TIMEOUT_SECONDS", ge=1
    )

    @field_validator("database_url")
    @classmethod
    # Make sure the DB URL uses the async driver we need.
    def database_url_must_use_asyncpg(cls, v: str) -> str:
        if not v.startswith("postgresql+asyncpg://"):
            raise ValueError("DATABASE_URL must start with postgresql+asyncpg://")
        if "<password>" in v or "<host>" in v:
            raise ValueError("DATABASE_URL still has placeholder values")
        return v

    @field_validator("app_base_url")
    @classmethod
    # Public URL must be a full http(s) address (used for webhooks and connect links).
    def app_base_url_must_be_absolute(cls, v: str) -> str:
        cleaned = v.rstrip("/")
        if not (
            cleaned.startswith("http://") or cleaned.startswith("https://")
        ):
            raise ValueError("APP_BASE_URL must start with http:// or https://")
        return cleaned

    @field_validator("connect_signing_key")
    @classmethod
    # Secret used to sign OAuth connect links — must be long enough to be safe.
    def connect_signing_key_must_be_nonempty(cls, v: str) -> str:
        if len(v) < 16:
            raise ValueError("CONNECT_SIGNING_KEY must be at least 16 characters")
        return v

    @field_validator("openrouter_api_key")
    @classmethod
    # OpenRouter rejects an empty key when constructing the provider.
    def openrouter_api_key_must_be_nonempty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("OPENROUTER_API_KEY must be non-empty")
        return v.strip()

    @field_validator("composio_api_key")
    @classmethod
    # Composio API key must be set for Gmail/Calendar integrations to work.
    def composio_api_key_must_be_nonempty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("COMPOSIO_API_KEY must be non-empty")
        return v.strip()


# Crash loudly at import time if config is bad
settings = Settings()
