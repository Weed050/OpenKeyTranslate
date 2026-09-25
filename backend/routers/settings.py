
# backend/routers/settings.py

from fastapi import APIRouter, HTTPException
import os
import json
import shutil
from models.schemas import SettingsSchema
from core.config import APP_ROOT_DIR, SETTINGS_FILE, load_settings, get_default_app_root

"""
API Router - right now used for managing application settings and data migration.

Provides endpoints to fetch, update, and handle the logic for moving 
the application root directory and its associated files.
"""

router = APIRouter(prefix="/settings", tags=["Settings"])

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

    # Explicitly default the migration flag to False for the UI
    data["migrate_data"] = False
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
    if new_settings.migrate_data and old_path != new_path:
        if not os.path.exists(old_path):
            raise HTTPException(status_code=404, detail="Source directory doesn't exist.")

        # Prevent automated migration of oversized directories
        size_mb = get_dir_size_mb(old_path)
        if size_mb > 500:  # 500MB safety threshold
            return {
                "message": f"Directory is too large ({size_mb:.2f} MB) for automatic migration. Please move it manually."
            }

        try:
            print(f"Migration started: {old_path} -> {new_path}")
            shutil.copytree(old_path, new_path, dirs_exist_ok=True)

            # Attempt to clean up the legacy directory
            try:
                shutil.rmtree(old_path)
                migration_msg = "Data migrated successfully."
            except PermissionError:
                # Common fallback when SQLite database file is still locked/active
                migration_msg = "Data copied. Please close the app to manually delete the old folder (database file is currently in use)."

        except Exception as e:

            # Rollback: Clean up incomplete target directory if copy fails
            if os.path.exists(new_path):
                shutil.rmtree(new_path)
            raise HTTPException(status_code=500, detail=f"Migration failed: {str(e)}")

    # Merge only the fields this endpoint owns into the FULL existing settings,
    # so providers/translation/memory/OCR config is never discarded.
    current_settings = load_settings()
    current_settings["app_root_dir"] = new_settings.app_root_dir
    current_settings["source_lang"] = new_settings.source_lang
    current_settings["target_lang"] = new_settings.target_lang

    with open(SETTINGS_FILE, "w", encoding="utf-8") as file:
        json.dump(current_settings, file, indent=4, ensure_ascii=False)

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

    with open(SETTINGS_FILE, "w", encoding="utf-8") as file:
        json.dump(current_settings, file, indent=4, ensure_ascii=False)

    return {"message": "Memory settings saved. Restart the application to apply changes.", "settings": current_settings}
