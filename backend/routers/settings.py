
# backend/routers/settings.py

from fastapi import APIRouter, HTTPException
import os
import re
import shutil
from models.schemas import SettingsSchema
from core.config import (APP_ROOT_DIR, load_settings, save_settings, get_default_app_root, APP_VERSION, DATABASE_PATH,
                         SETTINGS_FILE)

"""
API Router - right now used for managing application settings and data migration.

Provides endpoints to fetch, update, and handle the logic for moving 
the application root directory and its associated files.
"""

router = APIRouter(prefix="/settings", tags=["Settings"])

def _norm(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def _same_path(a: str, b: str) -> bool:
    """'C:/x/y' (Tk dialog) and 'C:\\x\\y' (APPDATA) are the same folder - plain string compare said 'different'."""
    return _norm(a) == _norm(b)


def _is_inside(child: str, parent: str) -> bool:
    c, p = _norm(child), _norm(parent)
    return c == p or c.startswith(p.rstrip(os.sep) + os.sep)


def get_dir_size_mb(path):
    """Calculate the total size of a directory in Megabytes (MB)."""
    total = 0
    for dirpath, _, filenames in os.walk(path):
        for f in filenames:
            total += os.path.getsize(os.path.join(dirpath, f))
    return total / (1024 * 1024)

@router.get("/")
async def get_settings():
    """Retrieve the current application settings."""
    data = load_settings()

    # API keys never leave the backend: with CORS open to any origin (see main.py) this endpoint would
    # otherwise hand them to any web page the browser opens. Only the last 4 chars are shown.
    for provider_cfg in data.get("providers", {}).values():
        for key_entry in provider_cfg.get("keys", []):
            if key_entry.get("api_key"):
                key_entry["api_key"] = "\u2022\u2022\u2022\u2022" + key_entry["api_key"][-4:]

    # Explicitly default the migration flag to False for the UI
    data["migrate_data"] = False
    from services.providers import PROVIDERS_INFO
    from services.providers.openai_compat_provider import PRESETS
    data["known_providers"] = {**PROVIDERS_INFO, "openai_compat": {**PROVIDERS_INFO["openai_compat"], "presets": PRESETS}}
    data["key_pool"] = _masked_pool()
    data["app_version"] = APP_VERSION
    data["log_file_path"] = os.path.join(APP_ROOT_DIR, "logs", "app.log")
    data["database_path"] = DATABASE_PATH
    data["settings_path"] = SETTINGS_FILE        # outside the project folder (core/config.py) - shown under "About"
    return data

@router.post("/")
async def update_settings(new_settings: SettingsSchema):
    """
    Update the workspace path (and source/target language) and optionally
    migrate data to a new root directory.

    IMPORTANT: this merges into the existing settings.json rather than
    overwriting it. SettingsSchema only knows about app_root_dir/
    source_lang/target_lang/migrate_data - it has no fields for
    providers/translation/memory/OCR settings, so a naive
    `json.dump(new_settings.model_dump(), ...)` would silently wipe every
    other setting (API keys included) on every workspace change. Loading
    the full current settings first and only overwriting the fields this
    endpoint actually manages avoids that.
    """
    old_path = APP_ROOT_DIR
    new_path = new_settings.app_root_dir
    migration_msg = "Settings saved successfully."

    # Handle data migration if requested and path has changed
    if new_settings.migrate_data and not _same_path(old_path, new_path):
        if not os.path.exists(old_path):
            raise HTTPException(status_code=404, detail="Source directory doesn't exist.")
        if _is_inside(new_path, old_path) or _is_inside(old_path, new_path):
            raise HTTPException(status_code=400, detail="New folder can't be inside the current workspace (or contain it).")
        target_existed = os.path.exists(new_path)

        # Prevent automated migration of oversized directories
        size_mb = get_dir_size_mb(old_path)
        if size_mb > 500:  # 500MB safety threshold
            return {
                "message": f"Directory is too large ({size_mb:.2f} MB) for automatic migration. Please move it manually."
            }

        try:
            print(f"Migration started: {old_path} -> {new_path}")
            shutil.copytree(old_path, new_path, dirs_exist_ok=True)
            # No database rewrite needed: project / chapter paths are derived from the workspace root at runtime.

            # Attempt to clean up the legacy directory
            try:
                shutil.rmtree(old_path)
                migration_msg = "Data migrated successfully."
            except PermissionError:
                # Common fallback when SQLite database file is still locked/active
                migration_msg = "Data copied. Please close the app to manually delete the old folder (database file is currently in use)."

        except Exception as e:

            # Rollback: remove the target ONLY if this call created it (it used to rmtree whatever was there -
            # including the live workspace when old/new were the same folder spelled differently).
            if not target_existed and os.path.exists(new_path):
                shutil.rmtree(new_path, ignore_errors=True)
            raise HTTPException(status_code=500, detail=f"Migration failed: {str(e)}")

    # Merge only the fields this endpoint owns into the FULL existing settings,
    # so providers/translation/memory/OCR config is never discarded.
    current_settings = load_settings()
    current_settings["app_root_dir"] = new_settings.app_root_dir
    current_settings["source_lang"] = new_settings.source_lang
    current_settings["target_lang"] = new_settings.target_lang

    save_settings(current_settings)

    return {"message": f"{migration_msg} Please restart the application to apply changes."}

@router.post("/reset-to-default")
async def reset_to_default():
    """Reset the workspace path to the default factory location (AppData)."""
    default_path = get_default_app_root()

    current_settings = load_settings()
    reset_payload = SettingsSchema(
        app_root_dir=default_path,
        source_lang=current_settings.get("source_lang", "en"),
        target_lang=current_settings.get("target_lang", "pl"),
        migrate_data=True
    )

    return await update_settings(reset_payload)

@router.post("/memory")
async def update_memory_settings(payload: dict):
    """Update correction-memory tuning (min word gate, similarity threshold) without touching the rest of settings.json."""
    current_settings = load_settings()

    if "memory_min_words" in payload:
        current_settings["memory_min_words"] = max(0, int(payload["memory_min_words"]))
    if "memory_similarity_threshold" in payload:
        current_settings["memory_similarity_threshold"] = float(payload["memory_similarity_threshold"])
    if "memory_top_k" in payload:
        current_settings["memory_top_k"] = max(1, int(payload["memory_top_k"]))
    if "memory_short_phrase_max_words" in payload:
        current_settings["memory_short_phrase_max_words"] = max(0, int(payload["memory_short_phrase_max_words"]))
    if "memory_lexical_min" in payload:
        current_settings["memory_lexical_min"] = min(100, max(0, int(payload["memory_lexical_min"])))
    if "memory_ab_sample_rate" in payload:
        current_settings["memory_ab_sample_rate"] = min(1.0, max(0.0, float(payload["memory_ab_sample_rate"])))
    for flag in ("memory_ab_test_logging", "memory_store_approved", "glossary_postedit",
                 "glossary_suggest_on_save", "translation_match_case"):
        if flag in payload:
            current_settings[flag] = bool(payload[flag])

    save_settings(current_settings)

    # NEVER return current_settings here: it contains the unmasked API keys (GET /settings/ masks them).
    return {"message": "Memory settings saved and applied."}


@router.post("/ignore-patterns")
async def update_ignore_patterns(payload: dict):
    """
    Replace the list of regexes that mark OCR bubbles as not worth translating
    (watermarks, site URLs, credits - see utils/ignore_filter.py). Each pattern
    is validated before anything is written, so one typo can never break OCR.
    """
    raw = payload.get("patterns", [])
    if not isinstance(raw, list):
        raise HTTPException(status_code=400, detail="patterns must be a list of strings")

    patterns = [str(p).strip() for p in raw if str(p).strip()]
    for pattern in patterns:
        try:
            re.compile(pattern)
        except re.error as e:
            raise HTTPException(status_code=400, detail=f"Invalid regex {pattern!r}: {e}")

    current_settings = load_settings()
    current_settings["ocr_ignore_patterns"] = patterns

    save_settings(current_settings)

    return {"message": f"Saved {len(patterns)} pattern(s). Applies to pages processed from now on (Re-OCR for existing pages).", "count": len(patterns)}

def _mask(key: str) -> str:
    return "\u2022\u2022\u2022\u2022" + key[-4:] if key else ""


def _masked_pool() -> list[dict]:
    """The key pool in order of use for the Settings page - keys masked, plus why a key cannot be used."""
    from services.providers import pool_entries, unusable_reason
    return [{"provider": e["provider"], "label": e["label"], "api_key": _mask(e["api_key"]), "model": e["own_model"],
             "base_url": e["base_url"] if e["provider"] == "openai_compat" else "", "enabled": e["enabled"],
             "problem": unusable_reason(e)}
            for e in pool_entries(include_disabled=True)]


@router.post("/translation")
async def update_translation_settings(payload: dict):
    """
    BYOK settings from the UI: translation on/off, the key pool, default model per provider (+ default server address
    of the OpenAI-compatible provider), temperature, max output tokens, Gemini thinking budget. Applied LIVE - the
    pool is rebuilt on the next translation.

    payload["pool"] = the keys IN ORDER OF USE: {provider, label, api_key, model, base_url, enabled, from_provider,
    from_label}. Stored back under providers.<name>.keys (where the secrets always lived) + key_order, and
    active_provider becomes "pool".

    A key sent back masked ("\u2022\u2022\u2022\u20221234", exactly what GET /settings/ returned) keeps the stored secret of
    from_provider / from_label - but ONLY while the key stays with the same provider and (OpenAI-compatible) the same
    server address. With CORS open to any origin (main.py), a web page could otherwise post a new base URL for a stored
    key and receive that key with the next translation; a moved key has to be pasted again. Response never has keys.
    """
    from services.providers import reset_provider, PROVIDER_NAMES

    current = load_settings()
    providers = current.setdefault("providers", {})

    if "translation_on" in payload:
        current["translation_on"] = bool(payload["translation_on"])
    if "translation_temperature" in payload:
        t = float(payload["translation_temperature"])
        if not 0.0 <= t <= 2.0:
            raise HTTPException(status_code=400, detail="Temperature must be 0.0-2.0")
        current["translation_temperature"] = t
    if "gemini_thinking_budget" in payload:
        current["gemini_thinking_budget"] = max(-1, int(payload["gemini_thinking_budget"]))
    if "translation_max_tokens" in payload:
        n = int(payload["translation_max_tokens"])
        if not 256 <= n <= 65536:
            raise HTTPException(status_code=400, detail="Max output tokens must be 256-65536")
        current["translation_max_tokens"] = n

    for name, model in (payload.get("provider_models") or {}).items():
        if name in PROVIDER_NAMES:
            providers.setdefault(name, {"model": "", "keys": []})["model"] = str(model or "").strip()
    notes = []
    if "pool" in payload:
        def effective_base(pname: str, k: dict) -> str:
            # per key only: a provider-wide default address could be changed without touching the key rows
            return (k.get("base_url") or "").strip().rstrip("/") if pname == "openai_compat" else ""

        # every stored secret BEFORE anything changes, by (provider, label), with the server it was sent to
        stored_all = {(pn, (k.get("label") or "default")): (k.get("api_key", ""), effective_base(pn, k))
                      for pn, c in providers.items() for k in (c or {}).get("keys", [])}
        new_keys = {name: [] for name in PROVIDER_NAMES}
        order = []
        for k in payload["pool"] or []:
            name = str(k.get("provider") or "")
            if name not in PROVIDER_NAMES:
                raise HTTPException(status_code=400, detail=f"Unknown provider '{name}'")
            label = (str(k.get("label") or "default")).strip() or "default"
            if any(e["label"] == label for e in new_keys[name]):
                raise HTTPException(status_code=400, detail=f"Two {name} keys are named '{label}' - names must differ")
            entry = {"label": label}
            base = str(k.get("base_url") or "").strip().rstrip("/") if name == "openai_compat" else ""
            if base:
                entry["base_url"] = base
            value = str(k.get("api_key") or "").strip()
            if value.startswith("\u2022"):                     # untouched masked value -> keep the stored secret ...
                origin = (str(k.get("from_provider") or name), str(k.get("from_label") or label))
                secret, sent_to = stored_all.get(origin, ("", ""))
                new_base = effective_base(name, entry)
                if origin[0] == name and (name != "openai_compat" or sent_to == new_base):
                    value = secret
                else:                                           # ... unless it would now go somewhere else
                    value = ""
                    if secret:
                        notes.append(f"Key '{label}': paste it again - it moved to another provider / server address.")
            entry["api_key"] = value
            key_model = str(k.get("model") or "").strip()
            if key_model:                                       # empty = the provider's default model
                entry["model"] = key_model
            entry["enabled"] = bool(k.get("enabled", True))
            new_keys[name].append(entry)
            order.append(f"{name}/{label}")
        for name in PROVIDER_NAMES:
            providers.setdefault(name, {"model": "", "keys": []})["keys"] = new_keys[name]
        current["key_order"] = order
        current["active_provider"] = "pool"

    save_settings(current)
    reset_provider()
    return {"message": "Translation settings saved and applied.", "notes": notes, "key_pool": _masked_pool()}


@router.post("/test-translation")
def test_translation():
    """One tiny request through the key pool as a translation would use it - 'Test the pool' on the Settings page."""
    import time
    from services.providers import get_provider
    start = time.monotonic()
    try:
        provider = get_provider()
        result = provider.translate([{"id": "t", "text": "Hello! Where are you going?"}])
    except Exception as e:
        return {"ok": False, "message": f"{type(e).__name__}: {e}"}
    ms = int((time.monotonic() - start) * 1000)
    text = result.get("t", "").strip()
    if not text:
        return {"ok": False, "message": "The provider answered but returned no translation."}
    return {"ok": True, "key": provider.current_key_label, "model": provider.current_model, "ms": ms, "sample": text,
            "message": f"Key '{provider.current_key_label}' ({provider.current_model}) answered in {ms / 1000:.1f} s: "
                       f"\"Hello! Where are you going?\" -> \"{text}\""}


@router.post("/test-key")
def test_one_key(payload: dict):
    """Test ONE key ({"id": "provider/label"}), switched on or not -> services.providers.test_key (no secrets)."""
    from services.providers import test_key
    return test_key(str(payload.get("id") or ""))


@router.get("/key-usage")
def key_usage():
    """Per key: requests made today, the day's limit when known and an estimate of what is left (no secrets) -
    services/providers/key_usage.summary; the usage line under each key in Settings."""
    from services.providers.key_usage import summary
    return {"keys": summary()}


@router.post("/key-models")
def key_models(payload: dict):
    """Models the server offers for one key ({"id": "provider/label"}) -> {"models": [...]} or {"error": "..."}."""
    from services.providers import list_models
    return list_models(str(payload.get("id") or ""))


@router.post("/ocr-engine")
async def update_ocr_engine(payload: dict):
    """
    The OCR engine (core/ocr_runtime.py): device, detection model, CPU threads, oneDNN. The model is built once when
    the backend starts, so these apply after a RESTART of the backend.
    """
    current = load_settings()
    choices = {"ocr_device": ("auto", "cpu", "gpu"), "ocr_det_model": ("auto", "server", "mobile")}
    for key, allowed in choices.items():
        if key in payload:
            if payload[key] not in allowed:
                raise HTTPException(status_code=400, detail=f"{key} must be one of {', '.join(allowed)}")
            current[key] = payload[key]
    if "ocr_cpu_threads" in payload:
        try:
            threads = int(payload["ocr_cpu_threads"] or 0)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="ocr_cpu_threads must be a whole number (0 = library default)")
        if not 0 <= threads <= 64:
            raise HTTPException(status_code=400, detail="ocr_cpu_threads must be 0..64")
        current["ocr_cpu_threads"] = threads
    if "ocr_mkldnn" in payload:
        current["ocr_mkldnn"] = bool(payload["ocr_mkldnn"])
    save_settings(current)
    return {"message": "Saved - restart the backend to load the OCR with these settings."}


@router.post("/ocr-options")
async def update_ocr_options(payload: dict):
    """Post-OCR options (utils/bubble_post.py). Apply to pages processed from now on - no restart."""
    current = load_settings()
    for key in ("ocr_textfix", "ocr_skip_numeric", "ocr_skip_symbols", "inpaint_use_interior", "ocr_refine", "ocr_scan_missed_bubbles",
                "ocr_detect_page_rotation"):
        if key in payload:
            current[key] = bool(payload[key])
    if "typesetting_uppercase" in payload:
        current["typesetting_uppercase"] = bool(payload["typesetting_uppercase"])
    if "typesetting_shape_fit" in payload:
        current["typesetting_shape_fit"] = bool(payload["typesetting_shape_fit"])
    if "typesetting_font" in payload:
        font = str(payload["typesetting_font"] or "").strip()
        if font and not os.path.isfile(font):            # a family name from Settings > Fonts, or a file path
            from services.font_service import find_family
            if not find_family(font):
                raise HTTPException(status_code=400, detail=f"Font not found (no such file or installed family): {font}")
        current["typesetting_font"] = font
    choices = {"inpaint_mode": ("glyph", "box"), "bubble_order": ("ttb", "rtl", "ltr")}
    for key, allowed in choices.items():
        if key in payload:
            if payload[key] not in allowed:
                raise HTTPException(status_code=400, detail=f"{key} must be one of {', '.join(allowed)}")
            current[key] = payload[key]
    save_settings(current)
    return {"message": "Saved. Applies to pages processed from now on (use Re-OCR for already processed pages)."}
