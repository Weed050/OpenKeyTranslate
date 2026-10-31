
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
    data["known_providers"] = ["groq", "gemini"]
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


@router.post("/translation")
async def update_translation_settings(payload: dict):
    """
    BYOK settings from the UI (before: only by hand-editing settings.json): translation on/off, active provider,
    per-provider default model + key pool (each key may carry its own "model"), temperature, max output tokens.
    Applied LIVE - the provider is rebuilt on the next translation.

    A key sent back masked ("\u2022\u2022\u2022\u20221234", exactly what GET /settings/ returned) keeps the stored secret
    for that label, so saving the form never wipes a key the browser never saw. A row moved to another provider in
    the UI sends from_provider / from_label, so its stored secret moves with it. Response never contains keys.
    """
    from services.providers import reset_provider

    current = load_settings()
    providers = current.setdefault("providers", {})

    if "translation_on" in payload:
        current["translation_on"] = bool(payload["translation_on"])
    if "active_provider" in payload:
        name = str(payload["active_provider"])
        if name not in ("groq", "gemini"):
            raise HTTPException(status_code=400, detail=f"Unknown provider '{name}'")
        current["active_provider"] = name
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

    # every stored secret BEFORE anything changes, by (provider, label): a row may have moved between providers
    stored_all = {(pn, k.get("label", "default")): k.get("api_key", "")
                  for pn, c in providers.items() for k in c.get("keys", [])}
    for name, cfg in (payload.get("providers") or {}).items():
        if name not in ("groq", "gemini"):
            continue
        slot = providers.setdefault(name, {"model": "", "keys": []})
        if "model" in cfg:
            slot["model"] = str(cfg["model"]).strip()
        if "keys" in cfg:
            new_keys, seen = [], set()
            for k in cfg["keys"]:
                label = (str(k.get("label") or "default")).strip() or "default"
                if label in seen:
                    raise HTTPException(status_code=400, detail=f"Duplicate key label '{label}' in {name}")
                seen.add(label)
                value = str(k.get("api_key") or "").strip()
                if value.startswith("\u2022"):                 # untouched masked value -> keep the stored secret
                    origin = (str(k.get("from_provider") or name), str(k.get("from_label") or label))
                    value = stored_all.get(origin, "")
                entry = {"label": label, "api_key": value}
                key_model = str(k.get("model") or "").strip()
                if key_model:                                   # empty = the provider's default model
                    entry["model"] = key_model
                new_keys.append(entry)
            slot["keys"] = new_keys or [{"label": "default", "api_key": ""}]

    save_settings(current)
    reset_provider()
    return {"message": "Translation settings saved and applied.", "active_provider": current.get("active_provider"),
            "providers": {n: {"model": c.get("model", ""),
                              "keys": [{"label": k.get("label"), "api_key": _mask(k.get("api_key", "")), "model": k.get("model", "")}
                                       for k in c.get("keys", [])]} for n, c in providers.items()}}


@router.post("/test-translation")
def test_translation():
    """Send one tiny request through the active provider/key pool - 'Test connection' button on the Settings page."""
    from services.providers import get_provider
    try:
        provider = get_provider()
        result = provider.translate([{"id": "t", "text": "Hello!"}])
    except Exception as e:
        return {"ok": False, "message": f"{type(e).__name__}: {e}"}
    text = result.get("t", "")
    return {"ok": bool(text.strip()), "message": f"OK via key '{provider.current_key_label}': \"Hello!\" -> \"{text}\"" if text.strip()
            else "The provider answered but returned no translation."}


@router.post("/ocr-options")
async def update_ocr_options(payload: dict):
    """Post-OCR options (utils/bubble_post.py). Apply to pages processed from now on - no restart."""
    current = load_settings()
    for key in ("ocr_textfix", "ocr_skip_numeric", "ocr_skip_symbols", "inpaint_use_interior", "ocr_refine", "ocr_scan_missed_bubbles"):
        if key in payload:
            current[key] = bool(payload[key])
    if "typesetting_uppercase" in payload:
        current["typesetting_uppercase"] = bool(payload["typesetting_uppercase"])
    if "typesetting_font" in payload:
        font = str(payload["typesetting_font"] or "").strip()
        if font and not os.path.isfile(font):
            raise HTTPException(status_code=400, detail=f"Font file not found: {font}")
        current["typesetting_font"] = font
    choices = {"inpaint_mode": ("glyph", "box"), "bubble_order": ("ttb", "rtl", "ltr")}
    for key, allowed in choices.items():
        if key in payload:
            if payload[key] not in allowed:
                raise HTTPException(status_code=400, detail=f"{key} must be one of {', '.join(allowed)}")
            current[key] = payload[key]
    save_settings(current)
    return {"message": "Saved. Applies to pages processed from now on (use Re-OCR for already processed pages)."}
