"""Shared model construction for concurrent agent runtimes.

This is intentionally runtime-neutral: it does not import a legacy agent,
tool registry, or conversation path.
"""

from pydantic_ai.models.openrouter import OpenRouterModel, OpenRouterModelSettings
from pydantic_ai.providers.openrouter import OpenRouterProvider

from app.core.config import settings


PERSONA_MODEL_SETTINGS = OpenRouterModelSettings(
    max_tokens=3000,
    openrouter_reasoning={"effort": "low"},
)


# Build an OpenRouter model instance with our API key.
def build_openrouter_model(model_name: str) -> OpenRouterModel:
    return OpenRouterModel(
        model_name,
        provider=OpenRouterProvider(
            api_key=settings.openrouter_api_key,
            app_url=settings.app_base_url,
            app_title="wpp-agent",
        ),
    )
