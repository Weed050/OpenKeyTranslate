
# backend/services/storage_service.py

"""
What OpenKeyTranslate keeps on this computer, where, and how big - Settings > Storage & uninstall.

Why: the app spreads over several folders (app data with the database and projects, a settings file outside the
project folder, gigabytes of OCR / embedding models in the libraries' caches in the home folder, logs), and a user
who wants to free space or uninstall - especially with the planned packaged .exe - cannot know about most of them.
This lists every place with its size and lets the user open it.

Deleting from here is limited on purpose to things that come back by themselves or are spare copies: logs, the
model caches (downloaded again on the next start), old copies of the settings file, leftovers of an import. The
work - database, projects, the settings file, the user's fonts - is NEVER deleted here (path + "Open" only; deleting
it is a manual step after closing the app, described in the uninstall steps). Paths are always computed here, never
taken from the request: the API is reachable from any web page the browser opens (CORS, main.py).
"""

import glob
import os
import shutil
import time

from core.config import APP_ROOT_DIR, DATABASE_PATH, ROOT_DIR, SETTINGS_FILE, WORKSPACE_DIR, get_setting

WALK_BUDGET_SECONDS = 6.0        # a huge folder (the program's .venv: tens of thousands of files) is counted only so long


def _size(paths: list[str], budget: float = WALK_BUDGET_SECONDS) -> dict:
    """{"bytes", "files", "approx"} of files / folders (approx = counting stopped after `budget` seconds)."""
    total = files = 0
    start = time.monotonic()
    for path in paths:
        if os.path.isfile(path):
            try:
                total += os.path.getsize(path)
                files += 1
            except OSError:
                pass
            continue
        for dirpath, _, names in os.walk(path):
            for name in names:
                try:
                    total += os.path.getsize(os.path.join(dirpath, name))
                    files += 1
                except OSError:
                    pass
            if time.monotonic() - start > budget:
                return {"bytes": total, "files": files, "approx": True}
    return {"bytes": total, "files": files, "approx": False}


def _hf_model_folder(hub: str) -> str:
    """The HuggingFace cache folder of the correction-memory model (sentence-transformers naming)."""
    name = get_setting("memory_embedding_model", "all-MiniLM-L6-v2") or "all-MiniLM-L6-v2"
    repo = name if "/" in name else f"sentence-transformers/{name}"
    return os.path.join(hub, "models--" + repo.replace("/", "--"))


def _items() -> list[dict]:
    """Every place, with the paths that make it up. Order = how the Settings page lists them."""
    from core.ocr_runtime import cache_dirs, models_root
    settings_dir = os.path.dirname(SETTINGS_FILE)
    caches = cache_dirs()
    own_models = models_root()                  # a folder chosen in settings = only this app's models live there
    hf_target = (caches["huggingface"] if own_models
                 else _hf_model_folder(os.path.join(caches["huggingface"], "hub")))
    paddle_target = caches["paddlex"] if own_models else os.path.join(caches["paddlex"], "official_models")
    exported = glob.glob(os.path.join(WORKSPACE_DIR, "*", "exported"))
    program_dir = os.path.dirname(ROOT_DIR)

    return [
        {"id": "projects", "group": "work", "title": "Projects",
         "what": "Page images, OCR results, cleaned pages and exports of every project. Your work.",
         "paths": [WORKSPACE_DIR], "folder": WORKSPACE_DIR, "deletable": False,
         "keep": "Delete a project on the Projects page; the whole folder only by hand when uninstalling.",
         "extra_paths": exported, "extra_label": "of it exported pages"},
        {"id": "database", "group": "work", "title": "Database",
         "what": "Projects, corrections memory, glossary, translation history (the study data).",
         "paths": [p for p in (DATABASE_PATH, DATABASE_PATH + "-wal", DATABASE_PATH + "-shm") if os.path.exists(p)]
                  or [DATABASE_PATH],
         "folder": DATABASE_PATH, "deletable": False,
         "keep": "Only by hand after closing the app - export a backup first if you may come back."},
        {"id": "settings", "group": "work", "title": "Settings file",
         "what": "All settings incl. your API keys. Kept outside the program folder so keys can never be committed.",
         "paths": [SETTINGS_FILE], "folder": SETTINGS_FILE, "deletable": False,
         "keep": "Only by hand after closing the app (the keys are gone with it)."},
        {"id": "fonts", "group": "work", "title": "Your fonts",
         "what": "Font files you added in Text on the page & fonts.",
         "paths": [os.path.join(APP_ROOT_DIR, "fonts")], "folder": os.path.join(APP_ROOT_DIR, "fonts"),
         "deletable": False, "keep": "Remove single fonts by hand in that folder."},
        {"id": "models_paddle", "group": "cache", "title": "OCR models (PaddleOCR)",
         "what": "Text detection / recognition models. Downloaded again on the next start of the backend (needs internet).",
         "paths": [paddle_target], "folder": paddle_target, "deletable": True,
         "shared": not own_models,
         "shared_note": "Shared with other programs that use PaddleOCR 3 on this computer - they download them again too."},
        {"id": "models_hf", "group": "cache", "title": "Correction memory model",
         "what": "The sentence embedding model of the memory (MiniLM). Downloaded again when the backend starts.",
         "paths": [hf_target], "folder": hf_target, "deletable": True,
         "note": "In use while the backend runs - its files may stay until you delete again after a restart."},
        {"id": "logs", "group": "cache", "title": "Logs",
         "what": "Application and error logs, crash traces, the OCR self-test report.",
         "paths": [os.path.join(APP_ROOT_DIR, "logs")], "folder": os.path.join(APP_ROOT_DIR, "logs"), "deletable": True,
         "note": "Today's log files are open while the backend runs and stay."},
        {"id": "settings_backups", "group": "cache", "title": "Old copies of the settings file",
         "what": "settings.json.bak (made at every save) and copies from the move out of the program folder. "
                 "They contain your API keys too.",
         "paths": sorted(glob.glob(os.path.join(settings_dir, "settings.json.bak"))
                         + glob.glob(os.path.join(settings_dir, "settings.json.tmp"))
                         + glob.glob(os.path.join(settings_dir, "legacy_settings.json*.bak"))),
         "folder": settings_dir, "deletable": True,
         "note": "A new settings.json.bak is written at the next save; it is the fallback for a damaged settings file."},
        {"id": "import_leftovers", "group": "cache", "title": "Data set aside by an import",
         "what": "\"Replace\" import moves your previous data here instead of deleting it (pre_import_backup_*), "
                 "plus unfinished import folders.",
         "paths": sorted(glob.glob(os.path.join(APP_ROOT_DIR, "pre_import_backup_*"))
                         + glob.glob(os.path.join(APP_ROOT_DIR, "_import_staging_*"))),
         "folder": APP_ROOT_DIR, "deletable": True},
        {"id": "program", "group": "program", "title": "The program",
         "what": "The app itself with its Python environment (backend\\.venv: Paddle, PyTorch...).",
         "paths": [program_dir], "folder": program_dir, "deletable": False,
         "keep": "Delete the folder by hand after closing the app (a packaged version: its uninstaller)."},
    ]


def storage_report() -> dict:
    """Everything for the Settings page: items with size + the folders to delete by hand when uninstalling."""
    out = []
    for item in _items():
        paths = [p for p in item.pop("paths") if p]
        extra = item.pop("extra_paths", None)
        size = _size(paths)
        out.append({**item, "exists": any(os.path.exists(p) for p in paths), "count": len(paths),
                    "bytes": size["bytes"], "files": size["files"], "approx": size["approx"],
                    **({"extra_bytes": _size(extra)["bytes"]} if extra else {})})
    settings_dir = os.path.dirname(SETTINGS_FILE)
    folders = [{"title": "App data (projects, database, logs, fonts)", "path": APP_ROOT_DIR}]
    if os.path.normcase(os.path.abspath(settings_dir)) != os.path.normcase(os.path.abspath(APP_ROOT_DIR)):
        folders.append({"title": "Settings folder", "path": settings_dir})
    folders.append({"title": "Program folder", "path": os.path.dirname(ROOT_DIR)})
    return {"items": out, "uninstall_folders": folders}


def delete_item(item_id: str) -> dict:
    """Delete a deletable item (see module doc). Files in use are skipped and counted. -> {"freed", "kept", "message"}."""
    item = next((i for i in _items() if i["id"] == item_id), None)
    if item is None or not item["deletable"]:
        raise ValueError(f"'{item_id}' cannot be deleted from the app.")
    freed = kept = 0
    for path in item["paths"]:
        if not os.path.exists(path):
            continue
        if os.path.isfile(path):
            files = [path]
        else:
            files = [os.path.join(d, n) for d, _, names in os.walk(path) for n in names]
        for f in files:
            try:
                size = os.path.getsize(f)
                os.remove(f)
                freed += size
            except OSError:
                kept += 1
        if os.path.isdir(path):
            for d, _, _ in sorted(os.walk(path), key=lambda w: -len(w[0])):     # empty folders, deepest first
                try:
                    os.rmdir(d)
                except OSError:
                    pass
            if not kept and os.path.isdir(path):
                shutil.rmtree(path, ignore_errors=True)
    mb = freed / (1024 * 1024)
    shown = f"{mb:.1f} MB" if mb >= 0.1 else f"{freed / 1024:.0f} KB"
    message = f"Freed {shown}." + (f" {kept} file(s) in use were kept - delete again after restarting the backend."
                                   if kept else "")
    print(f"[STORAGE] deleted '{item_id}': {shown}, {kept} file(s) kept")
    return {"freed": freed, "kept": kept, "message": message}
