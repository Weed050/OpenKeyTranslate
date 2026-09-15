
# backend/services/providers/__init__.py

"""
Provider factory for the translation backend.

Reads the active provider name and its config from settings and returns a
ready-to-use TranslationProvider instance (see base.py). Adding a new
backend (e.g. OpenAI, Claude) means writing one new provider class in this
package and registering it in _build_provider() below - translation_service.py
and everything upstream of it never needs to change.
"""

from core.config import PROVIDERS, ACTIVE_PROVIDER, TRANSLATION_TEMPERATURE, TRANSLATION_MAX_TOKENS
from .base import TranslationProvider

_provider_instance: TranslationProvider | None = None


def _build_provider() -> TranslationProvider:
    provider_cfg = PROVIDERS.get(ACTIVE_PROVIDER)
    if provider_cfg is None:
        raise ValueError(
            f"Unknown active_provider '{ACTIVE_PROVIDER}' — check the 'providers' keys in settings.json."
        )

    api_key = provider_cfg.get("api_key", "")
    model = provider_cfg.get("model", "")

    if ACTIVE_PROVIDER == "groq":
        from .groq_provider import GroqProvider
        return GroqProvider(api_key, model, TRANSLATION_TEMPERATURE, TRANSLATION_MAX_TOKENS)

    if ACTIVE_PROVIDER == "gemini":
        from .gemini_provider import GeminiProvider
        return GeminiProvider(api_key, model, TRANSLATION_TEMPERATURE, TRANSLATION_MAX_TOKENS)

    raise ValueError(f"No provider implementation registered for '{ACTIVE_PROVIDER}'.")


def get_provider() -> TranslationProvider:
    """Lazily build and cache the configured active provider (singleton)."""
    global _provider_instance
    if _provider_instance is None:
        _provider_instance = _build_provider()
    return _provider_instance
