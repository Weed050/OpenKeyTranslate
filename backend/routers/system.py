
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


@router.get("/tail-log")
async def tail_log(lines: int = 200, errors_only: bool = False):
    """Last N lines of the app log file, optionally filtered to ERROR-level only - for the in-app log viewer."""
    from core.config import APP_ROOT_DIR
    log_path = os.path.join(APP_ROOT_DIR, "logs", "app.log")
    if not os.path.exists(log_path):
        return {"lines": []}

    with open(log_path, "r", encoding="utf-8", errors="replace") as f:
        all_lines = f.readlines()

    if errors_only:
        all_lines = [ln for ln in all_lines if " ERROR " in ln]

    return {"lines": [ln.rstrip("\n") for ln in all_lines[-lines:]]}
