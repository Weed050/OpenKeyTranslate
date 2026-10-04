
# backend/services/providers/__init__.py

"""
Provider factory for the translation backend.

Reads the active provider name and its config from settings (LIVE - see core.config.get_setting) and returns a
ready-to-use TranslationProvider instance (see base.py). The cached instance is rebuilt automatically when the
provider / model / keys / temperature change, so editing them on the Settings page takes effect on the next
translation without restarting the app. Adding a new backend (e.g. OpenAI, Claude) means writing one new
provider class in this package and registering it in _build_provider() below.
"""

import json

from core.config import get_setting
from .base import TranslationProvider

_provider_instance: TranslationProvider | None = None
_provider_signature: str | None = None


def _current_config() -> tuple[str, dict, float, int]:
    active = get_setting("active_provider", "groq")
    provider_cfg = (get_setting("providers", {}) or {}).get(active)
    if provider_cfg is None:
        raise ValueError(f"Unknown active_provider '{active}' - check the 'providers' keys in settings.json.")
    return active, provider_cfg, get_setting("translation_temperature", 0.4), get_setting("translation_max_tokens", 4096)


def _build_provider() -> TranslationProvider:
    active, provider_cfg, temperature, max_tokens = _current_config()
    keys = provider_cfg.get("keys", [])
    model = provider_cfg.get("model", "")

    if active == "groq":
        from .groq_provider import GroqProvider
        return GroqProvider(keys, model, temperature, max_tokens)

    if active == "gemini":
        from .gemini_provider import GeminiProvider
        return GeminiProvider(keys, model, temperature, max_tokens)

    raise ValueError(f"No provider implementation registered for '{active}'.")


def reset_provider() -> None:
    """Drop the cached provider (next get_provider() rebuilds it)."""
    global _provider_instance, _provider_signature
    _provider_instance = None
    _provider_signature = None


def get_provider() -> TranslationProvider:
    """Lazily build and cache the configured active provider; rebuilt when its settings changed."""
    global _provider_instance, _provider_signature
    active, cfg, temperature, max_tokens = _current_config()
    signature = json.dumps([active, cfg, temperature, max_tokens], sort_keys=True, default=str)
    if _provider_instance is None or signature != _provider_signature:
        _provider_instance = _build_provider()
        _provider_signature = signature
    return _provider_instance
