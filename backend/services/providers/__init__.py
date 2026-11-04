
# backend/services/providers/__init__.py

"""
Translation backend factory: the key pool built from settings (LIVE - see core.config.get_setting).

Settings shape (secrets stay where they always were, so masking, backups and the docs secret scan need no change):
    providers.<name>.model           default model of that provider
    providers.<name>.keys[]          {"label", "api_key", optional "model", "base_url" (openai_compat: per key only),
                                     "enabled"}
    key_order                        ["provider/label", ...] - order of use across providers
    active_provider                  "pool" once the Settings page saved the pool; an older value (groq / gemini)
                                     still means "only that provider's keys", so an old settings.json behaves exactly
                                     as before without being rewritten.

get_provider() returns a cached KeyPool (pool.py), rebuilt when the pool / temperature / max tokens change, so the
Settings page applies on the next translation without a restart. test_key() and list_models() serve the Settings
page (one key, clear result). Adding a provider = one client module (call + list_models) + an entry in PROVIDERS.
"""

import json
import time

from core.config import get_setting
from .base import TranslationProvider, TranslationBatch, KeyCallError
from .pool import KeyPool, scrub

PROVIDER_NAMES = ("groq", "gemini", "openai_compat")

# UI facts per provider (Settings page). Model lists are only suggestions - "Models" asks the server what the key
# can really use; any name can be typed.
PROVIDERS_INFO = {
    "groq": {
        "label": "Groq", "key_url": "https://console.groq.com/keys", "needs_key": True,
        "models": ["openai/gpt-oss-120b", "openai/gpt-oss-20b", "llama-3.3-70b-versatile",
                   "moonshotai/kimi-k2-instruct", "qwen/qwen3-32b", "llama-3.1-8b-instant"],
    },
    "gemini": {
        "label": "Gemini", "key_url": "https://aistudio.google.com/apikey", "needs_key": True,
        "models": ["gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-2.5-pro", "gemini-2.0-flash"],
    },
    "openai_compat": {
        "label": "OpenAI-compatible", "beta": True, "needs_key": False,
        "models": [],
        "note": "Any server with the OpenAI chat API: OpenRouter, Mistral, DeepSeek, OpenAI, a local Ollama or "
                "LM Studio (no key needed there). Not tested with every server - if one misbehaves, please report "
                "it on GitHub.",
    },
}

_provider_instance: TranslationProvider | None = None
_provider_signature: str | None = None


def _client(provider: str):
    if provider == "groq":
        from . import groq_provider
        return groq_provider
    if provider == "gemini":
        from . import gemini_provider
        return gemini_provider
    if provider == "openai_compat":
        from . import openai_compat_provider
        return openai_compat_provider
    raise ValueError(f"No client for provider '{provider}'.")


def host_of(entry: dict) -> str:
    if entry["provider"] == "openai_compat":
        from .openai_compat_provider import host_of as compat_host
        return compat_host(entry)
    return {"groq": "api.groq.com", "gemini": "generativelanguage.googleapis.com"}.get(entry["provider"], "")


def key_id(provider: str, label: str) -> str:
    return f"{provider}/{label}"


def pool_entries(include_disabled: bool = False) -> list[dict]:
    """
    Every key in order of use: {"id", "provider", "label", "api_key", "model" (own or the provider default),
    "base_url", "enabled"}. Disabled keys only with include_disabled (Settings, health check).
    """
    providers = get_setting("providers", {}) or {}
    active = get_setting("active_provider", "groq")
    order = get_setting("key_order") or []
    rows = []
    for name, cfg in providers.items():
        cfg = cfg or {}
        for k in cfg.get("keys", []) or []:
            label = (k.get("label") or "default").strip() or "default"
            enabled = k.get("enabled")
            if enabled is None:                    # settings from before the pool: only the active provider's keys
                enabled = active == "pool" or name == active
            rows.append({
                "id": key_id(name, label), "provider": name, "label": label,
                "api_key": (k.get("api_key") or "").strip(),
                "model": (k.get("model") or cfg.get("model") or "").strip(),
                "own_model": (k.get("model") or "").strip(),
                # per key only (never a provider-wide default): the Settings router keeps a stored secret only while
                # its row's address is unchanged, so a key cannot be redirected without being pasted again
                "base_url": (k.get("base_url") or "").strip() if name == "openai_compat" else "",
                "enabled": bool(enabled),
            })
    if order:
        rank = {kid: i for i, kid in enumerate(order)}
        rows.sort(key=lambda r: rank.get(r["id"], len(rank)))
    else:
        rows.sort(key=lambda r: r["provider"] != active)          # stable: the active provider's keys first
    return rows if include_disabled else [r for r in rows if r["enabled"]]


def unusable_reason(entry: dict) -> str:
    """'' when the key can be used, else why not (shown in Settings and the health check)."""
    if entry["provider"] not in PROVIDER_NAMES:
        return f"unknown provider '{entry['provider']}'"
    if not entry["model"]:
        return "no model"
    if entry["provider"] == "openai_compat":
        return "" if entry["base_url"] else "no server address (base URL)"
    return "" if entry["api_key"] else "no key"


def _settings_signature(entries: list[dict]) -> str:
    return json.dumps([entries, get_setting("translation_temperature", 0.4), get_setting("translation_max_tokens", 4096),
                       get_setting("gemini_thinking_budget", 0)], sort_keys=True, default=str)


def _build_provider(entries: list[dict]) -> TranslationProvider:
    usable = [e for e in entries if not unusable_reason(e)]
    return KeyPool(usable, {name: _client(name) for name in {e["provider"] for e in usable}}, host_of,
                   get_setting("translation_temperature", 0.4), get_setting("translation_max_tokens", 4096))


def reset_provider() -> None:
    """Drop the cached pool (next get_provider() rebuilds it)."""
    global _provider_instance, _provider_signature
    _provider_instance = None
    _provider_signature = None


def get_provider() -> TranslationProvider:
    """Lazily build and cache the key pool; rebuilt when its settings changed."""
    global _provider_instance, _provider_signature
    entries = pool_entries()
    signature = _settings_signature(entries)
    if _provider_instance is None or signature != _provider_signature:
        _provider_instance = _build_provider(entries)
        _provider_signature = signature
    return _provider_instance


def find_entry(kid: str) -> dict | None:
    return next((e for e in pool_entries(include_disabled=True) if e["id"] == kid), None)


def test_key(kid: str) -> dict:
    """
    One tiny translation through ONE key, switched on or not (Settings: "Test" per key).
    -> {"ok", "kind" (ok / RateLimited / KeyRejected / Unreachable / Transient / error / unusable), "message",
        "model", "ms", "sample"}. Never contains the key.
    """
    entry = find_entry(kid)
    if entry is None:
        return {"ok": False, "kind": "unusable", "message": "Key not found - save the settings first."}
    reason = unusable_reason(entry)
    if reason:
        return {"ok": False, "kind": "unusable", "message": f"Can't test: {reason}.", "model": entry["model"]}
    from .prompts import SYSTEM_PROMPT
    source = "Hello! Where are you going?"
    start = time.monotonic()
    try:
        result = _client(entry["provider"]).call(
            entry, SYSTEM_PROMPT, json.dumps([{"id": "t", "text": source}]), TranslationBatch,
            get_setting("translation_temperature", 0.4), min(2048, int(get_setting("translation_max_tokens", 4096))))
    except KeyCallError as e:
        return {"ok": False, "kind": type(e).__name__, "message": scrub(str(e), entry), "model": entry["model"],
                "ms": int((time.monotonic() - start) * 1000)}
    except Exception as e:
        return {"ok": False, "kind": "error", "message": scrub(f"{type(e).__name__}: {e}", entry)[:500],
                "model": entry["model"], "ms": int((time.monotonic() - start) * 1000)}
    ms = int((time.monotonic() - start) * 1000)
    text = next((t.translation for t in result.translations if t.id == "t"), "").strip()
    if not text:
        return {"ok": False, "kind": "error", "model": entry["model"], "ms": ms,
                "message": "The server answered, but without a translation (the model may not follow JSON instructions)."}
    return {"ok": True, "kind": "ok", "model": entry["model"], "ms": ms, "source": source, "sample": text,
            "message": f"{entry['model']} answered in {ms / 1000:.1f} s"}


def list_models(kid: str) -> dict:
    """Models the server offers for this key -> {"models": [...]} or {"error": "..."}."""
    entry = find_entry(kid)
    if entry is None:
        return {"error": "Key not found - save the settings first."}
    if entry["provider"] != "openai_compat" and not entry["api_key"]:
        return {"error": "Paste the key first."}
    try:
        return {"models": _client(entry["provider"]).list_models(entry)}
    except Exception as e:
        return {"error": scrub(f"{type(e).__name__}: {e}" if not isinstance(e, KeyCallError) else str(e), entry)[:400]}
