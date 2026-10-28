
# backend/services/backup_service.py

"""
Export / import of ALL user data, for moving to another computer (or a clean reinstall).

BUNDLE (one .zip):
  manifest.json                  format version, app version, what is inside, project summary
  database/openkey_translate.db  consistent snapshot taken with SQLite's online backup API (safe while the app runs)
  settings/settings.json         every setting EXCEPT app_root_dir (a path of the old machine); API keys only if
                                 include_secrets
  env/.env                       the .env file, only if include_secrets
  workspace/<project>/raw|processed/...   all page images, OCR json, cleaned images
  models/<cache>/...             optional: HuggingFace + PaddleX caches (so the new PC needs no download)
  logs/...                       optional

IMPORT puts every part where THIS machine expects it (paths come from settings.json at runtime, the DB stores no
absolute paths), so nothing is hand-edited. Safety rules:
  - the bundle is validated first (zip-slip, manifest, DB integrity) in a staging folder; nothing live is touched
    until all of that passed
  - a target that already holds projects is only replaced with mode="replace", and then the OLD database + workspace
    are MOVED to <app root>/pre_import_backup_<timestamp>/ - never deleted
  - imports are refused while pages are being processed
  - a failure after the swap started puts the old data back
Long operations run as background jobs; the UI polls get_job().
"""

import json
import os
import platform
import shutil
import sqlite3
import tempfile
import threading
import time
import uuid
import zipfile
from datetime import datetime, timezone

from core.config import APP_ROOT_DIR, APP_VERSION, DATABASE_PATH, WORKSPACE_DIR, load_settings, save_settings
from core.ocr_runtime import cache_dirs
from utils.chapter_labels import IMG_EXTENSIONS

FORMAT_VERSION = 1
BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

M_MANIFEST = "manifest.json"
M_DB = "database/openkey_translate.db"
M_SETTINGS = "settings/settings.json"
M_ENV = "env/.env"
P_WORKSPACE = "workspace/"
P_MODELS = "models/"
P_LOGS = "logs/"

_STORE_EXT = IMG_EXTENSIONS + (".zip", ".gz", ".pdf")      # already compressed -> stored, deflating only burns CPU


class BackupError(Exception):
    """User-facing problem (message is shown as is)."""


# ------------------------------------------------------------------ background jobs

JOBS: dict[str, dict] = {}
_jobs_lock = threading.Lock()


def get_job(job_id: str) -> dict | None:
    return JOBS.get(job_id)


def _running_job() -> dict | None:
    return next((j for j in JOBS.values() if j["state"] == "running"), None)


def start_job(kind: str, fn, *args, **kwargs) -> str:
    """Run fn(job, *args) in a thread. Only one backup job at a time."""
    with _jobs_lock:
        if _running_job():
            raise BackupError("Another export/import is still running.")
        job_id = uuid.uuid4().hex[:10]
        job = {"id": job_id, "kind": kind, "state": "running", "done_bytes": 0, "total_bytes": 0,
               "step": "starting", "message": "", "result": None, "started": time.time()}
        JOBS[job_id] = job

    def _run():
        try:
            job["result"] = fn(job, *args, **kwargs)
            job["state"], job["message"] = "done", job["result"].get("message", "Done")
        except BackupError as e:
            job["state"], job["message"] = "error", str(e)
        except Exception as e:                                   # never leave a job "running" forever
            job["state"], job["message"] = "error", f"{type(e).__name__}: {e}"
            print(f"[BACKUP] job {kind} failed: {type(e).__name__}: {e}")
        job["finished"] = time.time()

    threading.Thread(target=_run, name=f"backup-{kind}", daemon=True).start()
    return job_id


# ------------------------------------------------------------------ helpers

def _find_env_file() -> str | None:
    for folder in (BACKEND_DIR, os.path.dirname(BACKEND_DIR)):
        candidate = os.path.join(folder, ".env")
        if os.path.isfile(candidate):
            return candidate
    return None


def _walk_files(root: str):
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            if name.endswith((".tmp", ".part")) or ".__tmp_" in name:
                continue
            yield os.path.join(dirpath, name)


def _db_summary(db_path: str) -> list[dict]:
    con = sqlite3.connect(db_path)
    try:
        rows = con.execute("SELECT id, name FROM projects ORDER BY name").fetchall()
        out = []
        for pid, name in rows:
            ch = con.execute("SELECT COUNT(*) FROM chapters WHERE project_id = ?", (pid,)).fetchone()[0]
            pg = con.execute("SELECT COUNT(*) FROM pages p JOIN chapters c ON c.id = p.chapter_id WHERE c.project_id = ?", (pid,)).fetchone()[0]
            co = con.execute("SELECT COUNT(*) FROM corrections WHERE project_id = ?", (pid,)).fetchone()[0]
            gl = con.execute("SELECT COUNT(*) FROM glossary_terms WHERE project_id = ?", (pid,)).fetchone()[0]
            out.append({"name": name, "chapters": ch, "pages": pg, "corrections": co, "glossary_terms": gl})
        return out
    finally:
        con.close()


def _sanitized_settings(include_secrets: bool) -> dict:
    data = json.loads(json.dumps(load_settings()))             # deep copy
    data.pop("app_root_dir", None)                              # a path of THIS machine - never exported
    models_dir = data.get("models_dir", "")
    if models_dir and os.path.isabs(models_dir):
        data["models_dir"] = ""                                 # absolute path of this machine
    if not include_secrets:
        for provider in (data.get("providers") or {}).values():
            for key in provider.get("keys", []):
                key["api_key"] = ""
    return data


def _current_has_data() -> bool:
    if not os.path.exists(DATABASE_PATH):
        return False
    try:
        con = sqlite3.connect(DATABASE_PATH)
        try:
            return con.execute("SELECT COUNT(*) FROM projects").fetchone()[0] > 0
        finally:
            con.close()
    except sqlite3.Error:
        return False


def _busy_pages() -> int:
    if not os.path.exists(DATABASE_PATH):
        return 0
    try:
        con = sqlite3.connect(DATABASE_PATH)
        try:
            return con.execute("SELECT COUNT(*) FROM pages WHERE status IN ('queued','processing')").fetchone()[0]
        finally:
            con.close()
    except sqlite3.Error:
        return 0


# ------------------------------------------------------------------ export

def create_backup(job: dict, dest_path: str, include_secrets: bool = True, include_models: bool = False,
                  include_logs: bool = False) -> dict:
    dest_path = os.path.abspath(dest_path)
    if not dest_path.lower().endswith(".zip"):
        dest_path += ".zip"
    folder = os.path.dirname(dest_path)
    if not os.path.isdir(folder):
        raise BackupError(f"Destination folder does not exist: {folder}")
    ws = os.path.abspath(WORKSPACE_DIR)
    if os.path.commonpath([os.path.abspath(dest_path), ws]) == ws:
        raise BackupError("Choose a destination OUTSIDE the workspace folder (the backup would contain itself).")

    tmpdir = tempfile.mkdtemp(prefix="okt_export_")
    part = dest_path + ".part"
    try:
        # 1. consistent DB snapshot (works while the app is running and the DB is in WAL mode)
        job["step"] = "database snapshot"
        snap = os.path.join(tmpdir, "db.sqlite")
        src = sqlite3.connect(DATABASE_PATH)
        dst = sqlite3.connect(snap)
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
        projects = _db_summary(snap)

        # 2. file plan
        job["step"] = "scanning files"
        plan: list[tuple[str, str]] = []                        # (source file, name inside zip)
        settings_path = os.path.join(tmpdir, "settings.json")
        with open(settings_path, "w", encoding="utf-8") as f:
            json.dump(_sanitized_settings(include_secrets), f, indent=2, ensure_ascii=False)
        plan += [(snap, M_DB), (settings_path, M_SETTINGS)]
        env_file = _find_env_file() if include_secrets else None
        if env_file:
            plan.append((env_file, M_ENV))
        if os.path.isdir(WORKSPACE_DIR):
            plan += [(p, P_WORKSPACE + os.path.relpath(p, WORKSPACE_DIR).replace(os.sep, "/")) for p in _walk_files(WORKSPACE_DIR)]
        if include_models:
            for label, folder_ in cache_dirs().items():
                if os.path.isdir(folder_):
                    plan += [(p, f"{P_MODELS}{label}/" + os.path.relpath(p, folder_).replace(os.sep, "/")) for p in _walk_files(folder_)]
        logs_dir = os.path.join(APP_ROOT_DIR, "logs")
        if include_logs and os.path.isdir(logs_dir):
            plan += [(p, P_LOGS + os.path.relpath(p, logs_dir).replace(os.sep, "/")) for p in _walk_files(logs_dir)]
        job["total_bytes"] = sum(os.path.getsize(p) for p, _ in plan)

        manifest = {
            "format_version": FORMAT_VERSION, "app_version": APP_VERSION,
            "created_at": datetime.now(timezone.utc).isoformat(), "source_platform": platform.system(),
            "has_secrets": bool(include_secrets), "has_env": bool(env_file), "has_models": bool(include_models),
            "has_logs": bool(include_logs), "file_count": len(plan), "total_bytes": job["total_bytes"],
            "projects": projects,
        }

        # 3. write
        job["step"] = "writing archive"
        with zipfile.ZipFile(part, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as z:
            z.writestr(M_MANIFEST, json.dumps(manifest, indent=2, ensure_ascii=False))
            for source, arcname in plan:
                ctype = zipfile.ZIP_STORED if arcname.lower().endswith(_STORE_EXT) else zipfile.ZIP_DEFLATED
                try:
                    z.write(source, arcname, compress_type=ctype)
                except FileNotFoundError:                      # a temp file vanished mid-scan (OCR running): skip it
                    continue
                job["done_bytes"] += os.path.getsize(source) if os.path.exists(source) else 0
        os.replace(part, dest_path)
    except Exception:
        if os.path.exists(part):
            os.remove(part)
        raise
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    size_mb = os.path.getsize(dest_path) / (1024 * 1024)
    return {"message": f"Exported {len(projects)} project(s), {manifest['file_count']} file(s), {size_mb:.1f} MB to {dest_path}",
            "path": dest_path, "manifest": manifest}


# ------------------------------------------------------------------ import

def _read_manifest(zf: zipfile.ZipFile) -> dict:
    try:
        manifest = json.loads(zf.read(M_MANIFEST))
    except KeyError:
        raise BackupError("This is not an OpenKeyTranslate backup (manifest.json is missing).")
    if manifest.get("format_version", 0) > FORMAT_VERSION:
        raise BackupError(f"Backup format {manifest['format_version']} is newer than this app understands ({FORMAT_VERSION}). Update the app first.")
    return manifest


def inspect_backup(path: str) -> dict:
    """What is inside, and what importing it here would do. Reads only the manifest."""
    if not os.path.isfile(path) or not zipfile.is_zipfile(path):
        raise BackupError("That file is not a zip archive.")
    with zipfile.ZipFile(path) as zf:
        manifest = _read_manifest(zf)
    existing = _current_has_data()
    return {"manifest": manifest, "target_has_data": existing, "busy_pages": _busy_pages(),
            "needs_replace": existing, "workspace": WORKSPACE_DIR, "database": DATABASE_PATH}


def _safe_extract(zf: zipfile.ZipFile, staging: str, job: dict) -> None:
    root = os.path.realpath(staging)
    members = [m for m in zf.infolist() if not m.is_dir()]
    job["total_bytes"] = sum(m.file_size for m in members)
    for m in members:
        target = os.path.realpath(os.path.join(staging, m.filename))
        if os.path.commonpath([target, root]) != root:           # zip-slip: "../../x" or absolute names
            raise BackupError(f"Unsafe path in archive: {m.filename}")
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with zf.open(m) as src, open(target, "wb") as out:
            shutil.copyfileobj(src, out, 1024 * 1024)
        job["done_bytes"] += m.file_size


def restore_backup(job: dict, path: str, mode: str = "fresh", restore_settings: bool = True,
                   restore_env: bool = True) -> dict:
    info = inspect_backup(path)
    manifest = info["manifest"]
    if info["busy_pages"]:
        raise BackupError(f"{info['busy_pages']} page(s) are being processed. Wait for them to finish, then import.")
    if info["target_has_data"] and mode != "replace":
        raise BackupError("This installation already contains projects. Choose 'replace' to import (the current data "
                          "is moved aside, not deleted).")

    stamp = time.strftime("%Y%m%d-%H%M%S")
    staging = os.path.join(APP_ROOT_DIR, f"_import_staging_{stamp}")
    aside = os.path.join(APP_ROOT_DIR, f"pre_import_backup_{stamp}")
    moved: list[tuple[str, str]] = []                            # (original, where it went) for rollback
    try:
        job["step"] = "unpacking"
        os.makedirs(staging)
        with zipfile.ZipFile(path) as zf:
            _safe_extract(zf, staging, job)

        job["step"] = "checking the database"
        staged_db = os.path.join(staging, *M_DB.split("/"))
        if not os.path.isfile(staged_db):
            raise BackupError("The backup has no database.")
        con = sqlite3.connect(staged_db)
        try:
            if con.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise BackupError("The database inside the backup is damaged (integrity check failed).")
            if not con.execute("SELECT 1 FROM sqlite_master WHERE name = 'projects'").fetchone():
                raise BackupError("The database inside the backup has no 'projects' table.")
        finally:
            con.close()

        staged_ws = os.path.join(staging, "workspace")
        staged_names = os.listdir(staged_ws) if os.path.isdir(staged_ws) else []

        job["step"] = "installing"
        from core.database import engine
        engine.dispose()                                         # release the live DB file before replacing it
        os.makedirs(aside, exist_ok=True)

        def move_aside(p: str, label: str) -> None:
            if os.path.exists(p):
                dest = os.path.join(aside, label)
                shutil.move(p, dest)
                moved.append((p, dest))

        if mode == "replace":
            for suffix in ("", "-wal", "-shm"):
                move_aside(DATABASE_PATH + suffix, "db" + suffix + ".bak")
            move_aside(WORKSPACE_DIR, "projects_workspace")
        else:
            conflicts = [n for n in staged_names if os.path.exists(os.path.join(WORKSPACE_DIR, n))]
            if conflicts:
                raise BackupError(f"Folders already exist in the workspace: {', '.join(conflicts[:5])}")
            for suffix in ("-wal", "-shm"):
                if os.path.exists(DATABASE_PATH + suffix):
                    os.remove(DATABASE_PATH + suffix)
            if os.path.exists(DATABASE_PATH):
                move_aside(DATABASE_PATH, "db.bak")              # empty DB of a fresh install

        os.makedirs(WORKSPACE_DIR, exist_ok=True)
        shutil.copy2(staged_db, DATABASE_PATH)
        for name in staged_names:
            shutil.move(os.path.join(staged_ws, name), os.path.join(WORKSPACE_DIR, name))

        # models (merge, never overwrite what is already there)
        for label, target in cache_dirs().items():
            src = os.path.join(staging, "models", label)
            if os.path.isdir(src):
                shutil.copytree(src, target, dirs_exist_ok=True)

        # settings: imported values, but THIS machine's workspace root stays; keys only if the bundle carried them
        notes = []
        staged_settings = os.path.join(staging, *M_SETTINGS.split("/"))
        if restore_settings and os.path.isfile(staged_settings):
            with open(staged_settings, "r", encoding="utf-8") as f:
                imported = json.load(f)
            current = load_settings()
            keep_keys = not manifest.get("has_secrets")
            for key, value in imported.items():
                if key in ("app_root_dir",) or (key == "providers" and keep_keys):
                    continue
                current[key] = value
            if keep_keys:
                notes.append("API keys were not in the backup - your current keys were kept")
            save_settings(current)
        staged_env = os.path.join(staging, *M_ENV.split("/"))
        if restore_env and os.path.isfile(staged_env):
            env_target = _find_env_file() or os.path.join(BACKEND_DIR, ".env")
            if os.path.exists(env_target):
                shutil.copy2(env_target, env_target + ".bak")
            shutil.copy2(staged_env, env_target)
            notes.append(".env restored (restart the backend for it to take effect)")

        job["step"] = "reconciling"
        report = _post_import_reconcile()
    except Exception:
        # roll back whatever was moved aside so the old data is live again
        try:
            from core.database import engine
            engine.dispose()
            for suffix in ("", "-wal", "-shm"):
                if os.path.exists(DATABASE_PATH + suffix) and any(o == DATABASE_PATH + suffix for o, _ in moved):
                    os.remove(DATABASE_PATH + suffix)
            if any(o == WORKSPACE_DIR for o, _ in moved) and os.path.isdir(WORKSPACE_DIR):
                shutil.rmtree(WORKSPACE_DIR, ignore_errors=True)
            for original, dest in reversed(moved):
                if os.path.exists(dest):
                    shutil.move(dest, original)
        except Exception as rb:                                  # pragma: no cover - last resort
            print(f"[BACKUP] ROLLBACK FAILED: {rb}. Old data is in {aside}")
        raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        if os.path.isdir(aside) and not os.listdir(aside):
            os.rmdir(aside)

    msg = f"Imported {len(manifest.get('projects', []))} project(s)."
    if moved:
        msg += f" The previous data was moved to {aside}."
    return {"message": msg, "notes": notes, "reconcile": report, "aside": aside if moved else None}


def _post_import_reconcile() -> dict:
    """Bring the imported DB up to this version's schema and make the DB agree with the files on disk."""
    from core.database import SessionLocal, init_db
    from models.models import Page
    from services.reconcile import reconcile_chapters, reconcile_projects
    init_db()                                                    # create missing tables, add missing columns
    db = SessionLocal()
    try:
        projects = reconcile_projects(db)
        chapters = reconcile_chapters(db)
        stale = db.query(Page).filter(Page.status.in_(("queued", "processing"))).update(
            {Page.status: "pending"}, synchronize_session=False)
        db.commit()
        return {"projects": projects, "chapters": chapters, "stale_reset": stale}
    finally:
        db.close()
