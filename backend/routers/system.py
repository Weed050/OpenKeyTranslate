
# backend/routers/system.py

"""
Small utility endpoints for OS-level integration.

Safe only because this server is bound to 127.0.0.1 and this is a
single-user local tool (see main.py's CORS comment for the same
reasoning) - there is no path allowlist here, any path on the machine can
be opened. Do not expose this server beyond localhost.
"""

import os
import platform
import subprocess
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/system", tags=["System"])


class OpenPathRequest(BaseModel):
    path: str


class ExportRequest(BaseModel):
    path: str
    include_secrets: bool = True      # API keys in settings.json + the .env file
    include_models: bool = False      # HuggingFace / PaddleX caches (big, but the new PC then needs no download)
    include_logs: bool = False


class ImportRequest(BaseModel):
    path: str
    mode: str = "fresh"               # "fresh" (target must be empty) | "replace" (old data is moved aside, not deleted)
    restore_settings: bool = True
    restore_env: bool = True


@router.post("/open-path")
async def open_path(payload: OpenPathRequest):
    """
    Open a file or folder in the OS's native file explorer.

    Directories are opened directly. Files are *revealed* (selected inside
    their containing folder) rather than opened with their default
    handler - e.g. for a translation JSON, seeing it highlighted in
    Explorer is generally more useful than it popping open in a text
    editor/browser.
    """
    target = payload.path
    if not os.path.exists(target):
        raise HTTPException(status_code=404, detail=f"Path not found: {target}")

    system = platform.system()
    try:
        if system == "Windows":
            if os.path.isdir(target):
                os.startfile(target)
            else:
                subprocess.Popen(f'explorer /select,"{target}"')
        elif system == "Darwin":
            subprocess.Popen(["open", "-R", target] if os.path.isfile(target) else ["open", target])
        else:
            folder = target if os.path.isdir(target) else os.path.dirname(target)
            subprocess.Popen(["xdg-open", folder])
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Couldn't open path: {e}")

    return {"message": "Opened"}


@router.get("/health")
async def health():
    """Config/connectivity self-check (see core/startup_checks.py) - the frontend shows a banner for error/warn."""
    from core.startup_checks import run_checks
    from services.reconcile import LAST_REPORT
    issues = run_checks(check_network=True)
    from core.ocr_runtime import RUNTIME
    if RUNTIME.get("requested") == "gpu" and RUNTIME.get("device") == "cpu":
        issues.append({"level": "warn", "code": "ocr_gpu_fallback", "message": f"OCR runs on CPU: {RUNTIME.get('reason')}."})
    from services.memory_service import EMBEDDER_STATE
    if EMBEDDER_STATE["state"] == "error":
        issues.append({"level": "warn", "code": "embedder_failed",
                       "message": f"Correction memory model failed to load ({EMBEDDER_STATE['error']}). Translation still works, "
                                  f"but memory hints and saving corrections will fail until it loads (needs internet once to download)."})
    # things the startup reconcile found but only wrote to the log (missing page files, chapters kept without raw/ ...)
    for i, line in enumerate(LAST_REPORT.get("warnings", [])[:5]):
        issues.append({"level": "warn", "code": f"reconcile_{i}", "message": f"Disk vs DB: {line} (page list -> Rescan folders)"})
    return {"issues": issues}


@router.get("/tail-log")
async def tail_log(lines: int = 200, errors_only: bool = False):
    """Last N lines of today's log file (errors.log when errors_only, else app.log) - for the in-app log viewer."""
    from datetime import datetime
    from core.config import APP_ROOT_DIR

    month_dir = os.path.join(APP_ROOT_DIR, "logs", datetime.now().strftime("%Y-%m"))
    filename = "errors.log" if errors_only else "app.log"
    log_path = os.path.join(month_dir, filename)
    if not os.path.exists(log_path):
        return {"lines": []}

    with open(log_path, "r", encoding="utf-8", errors="replace") as f:
        all_lines = f.readlines()

    return {"lines": [ln.rstrip("\n") for ln in all_lines[-lines:]]}


# ---------------------------------------------------------------- export / import (moving to another computer)

def _tk_dialog(kind: str, **options) -> str:
    import tkinter
    from tkinter import filedialog
    root = tkinter.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        if kind == "save":
            return filedialog.asksaveasfilename(**options) or ""
        return filedialog.askopenfilename(**options) or ""
    finally:
        root.destroy()


# async on purpose (like /projects/select-folder): Tk wants the main thread, which is the event loop's thread.
@router.get("/pick-save-file")
async def pick_save_file(default_name: str = "OpenKeyTranslate-backup.zip"):
    """Native 'Save as...' dialog for the export. Returns {"path": ""} when cancelled."""
    path = _tk_dialog("save", title="Save the backup as...", defaultextension=".zip", initialfile=default_name,
                      filetypes=[("OpenKeyTranslate backup", "*.zip")])
    return {"path": path}


@router.get("/pick-open-file")
async def pick_open_file():
    """Native 'Open' dialog for the import. Returns {"path": ""} when cancelled."""
    path = _tk_dialog("open", title="Choose a backup to import", filetypes=[("OpenKeyTranslate backup", "*.zip")])
    return {"path": path}


@router.post("/export")
def export_all(payload: ExportRequest):
    """Start exporting everything (DB, settings, .env, projects, optionally model caches) into one zip. Poll /system/jobs/{id}."""
    from services import backup_service as bs
    try:
        job_id = bs.start_job("export", bs.create_backup, payload.path, payload.include_secrets,
                              payload.include_models, payload.include_logs)
    except bs.BackupError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {"job_id": job_id}


@router.post("/import/inspect")
def import_inspect(payload: OpenPathRequest):
    """Read a backup's manifest and say what importing it here would do (nothing is changed)."""
    from services import backup_service as bs
    try:
        return bs.inspect_backup(payload.path)
    except bs.BackupError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/import")
def import_all(payload: ImportRequest):
    """Start importing a backup. Poll /system/jobs/{id}."""
    from services import backup_service as bs
    if payload.mode not in ("fresh", "replace"):
        raise HTTPException(status_code=400, detail="mode must be 'fresh' or 'replace'")
    try:
        bs.inspect_backup(payload.path)          # fail fast with a clear message before a thread is started
        job_id = bs.start_job("import", bs.restore_backup, payload.path, payload.mode,
                              payload.restore_settings, payload.restore_env)
    except bs.BackupError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {"job_id": job_id}


@router.get("/jobs/{job_id}")
def job_status(job_id: str):
    from services import backup_service as bs
    job = bs.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Unknown job")
    return job
