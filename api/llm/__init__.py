"""LLM providers behind one interface. Nothing outside this package imports a
provider SDK."""

from __future__ import annotations

import logging

from api.core.config import Settings
from api.llm.base import LLMProvider

logger = logging.getLogger(__name__)


def build_provider(settings: Settings) -> LLMProvider:
    """Chosen by LLM_PROVIDER. Imported lazily, so a fake-only process never
    loads the Gemini SDK."""
    if settings.llm_provider == "gemini":
        from api.llm.gemini import GeminiProvider

        return GeminiProvider(
            api_key=settings.gemini_api_key.get_secret_value(),
            model=settings.gemini_model,
            thinking_budget=settings.gemini_thinking_budget,
            timeout_seconds=settings.llm_timeout_seconds,
        )

    from api.llm.fake import FakeProvider

    return FakeProvider(delay=settings.fake_llm_delay_seconds)


def report_status(provider: LLMProvider) -> None:
    if getattr(provider, "configured", True):
        model = getattr(provider, "model", "-")
        logger.info("AI provider: %s (%s)", provider.name, model)
    else:
        logger.warning(
            "LLM_PROVIDER=%s but no API key is set; every AI request will fail "
            "with ai.invalid_key until GEMINI_API_KEY is configured.",
            provider.name,
        )
