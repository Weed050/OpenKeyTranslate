
# backend/routers/projects.py

"""
API Router used for managing projects and file imports.

Handles fetching project lists, native directory selection via Tkinter,
batch importing images, and synchronizing the database with the filesystem.
"""

from tkinter import filedialog
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List
import os
import shutil
import tkinter
from models.models import Project, Chapter
from models.schemas import ProjectSchema
from core.database import get_db
from core.config import WORKSPACE_DIR
from utils.chapter_labels import sanitize_label
from services.chapter_import import auto_selections, import_selected

# Initialize the router for project-related endpoints
router = APIRouter(
    prefix="/projects",
    tags=["Projects"]
)

@router.get("/", response_model=List[ProjectSchema])
async def get_projects(db: Session = Depends(get_db)):
    """Retrieve a list of all projects currently in the database."""

    projects = db.query(Project).all()
    return projects

@router.get("/{project_id}/progress")
def project_progress(project_id: int, db: Session = Depends(get_db)):
    """
    Pages of the project (not excluded) per work stage - the stage bar on the project card, same rule as the chapter
    bars (services/page_progress.py): {"total", "stages": {stage: n}, "processed", "reviewed", "last_activity"}.
    """
    from services.page_progress import project_progress
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project_progress(project)


@router.get("/{project_id}/prefs")
def get_project_prefs(project_id: int, db: Session = Depends(get_db)):
    """Project-wide choices (services/project_prefs.py): {"bubble_order": "rtl" | null, "lettering_style": {...}}."""
    from services import project_prefs
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return {"bubble_order": project_prefs.bubble_order(project.workspace_path),
            "lettering_style": project_prefs.lettering_style(project.workspace_path)}


@router.patch("/{project_id}/prefs")
def set_project_prefs(project_id: int, payload: dict, db: Session = Depends(get_db)):
    """
    {"lettering_style": {font, bold, italic, stroke, color} | null} - Lettering "Use in whole project" (null = back to
    the Settings font and plain black letters); {"bubble_order": "ttb" | "rtl" | "ltr" | null} - order of pages OCR'd
    from now on. Bubbles with a style of their own and pages already sorted keep theirs.
    """
    from services import project_prefs
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    patch = {}
    if "lettering_style" in (payload or {}):
        patch["lettering_style"] = project_prefs.clean_style(payload["lettering_style"]) or None
    if "bubble_order" in (payload or {}):
        order = payload["bubble_order"]
        if order is not None and order not in project_prefs.ORDERS:
            raise HTTPException(status_code=400, detail="bubble_order must be ttb, rtl or ltr")
        patch["bubble_order"] = order
    project_prefs.save(project.workspace_path, patch)
    return get_project_prefs(project_id, db)


@router.post("/{project_id}/replace-translations")
def replace_translations(project_id: int, payload: dict, db: Session = Depends(get_db)):
    """
    Find & replace in the bubbles' translations of the whole project (services/translation_replace.py):
    {"find", "replace", "mode": "word" | "prefix", "keys": ["page_id:bubble_id", ...] (optional), "apply": false}
    -> {"applied", "count", "changes": [...]}; apply=false is a preview.
    """
    from services.translation_replace import replace_in_translations
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    find = (payload or {}).get("find") or ""
    if len(find.strip()) < 2:
        raise HTTPException(status_code=422, detail="Search text must have at least 2 characters")
    keys = payload.get("keys")
    if keys is not None and not (isinstance(keys, list) and all(isinstance(k, str) for k in keys)):
        raise HTTPException(status_code=400, detail="keys must be a list of 'page_id:bubble_id'")
    mode = "prefix" if payload.get("mode") == "prefix" else "word"
    changes = replace_in_translations(project, find, payload.get("replace") or "", mode, keys, bool(payload.get("apply")))
    return {"applied": bool(payload.get("apply")), "count": len(changes), "changes": changes}


@router.get("/select-folder")
async def select_folder():
    """
    Open a native OS directory selection dialog using Tkinter.
    Returns the absolute path of the selected folder.
    """

    root = tkinter.Tk()
    root.withdraw() # Hide the main Tkinter window
    root.attributes('-topmost', True) # Ensure the dialog appears on top of other windows
    folder_path = filedialog.askdirectory(title="Select a folder with images")
    root.destroy()
    return {"path": folder_path}

@router.post("/import")
async def import_folder(data: dict, db: Session = Depends(get_db)):
    """
    Import a folder from the local filesystem to create a new project.

    Chapter labels come from the SOURCE FOLDER NAMES (chapter_13_5 -> Chapter_13.5),
    not from a counter - see utils/chapter_labels.py and services/chapter_import.py.
    """

    actual_source_path = data.get("path")
    project_name = sanitize_label((data.get("projectName") or "").strip(), default="Project") if (data.get("projectName") or "").strip() else ""

    if not actual_source_path or not project_name:
        raise HTTPException(status_code=400, detail="Missing path or project name")

    target_project_path = os.path.join(WORKSPACE_DIR, project_name)
    raw_dir = os.path.join(target_project_path, "raw")
    processed_dir = os.path.join(target_project_path, "processed")

    # These checks MUST stay outside the try/except below. Inside it, the 400 was caught by
    # `except Exception`, which then rmtree'd target_project_path - i.e. re-using an existing
    # project name DELETED that project's files from disk.
    if db.query(Project).filter(Project.name == project_name).first() or os.path.exists(target_project_path):
        raise HTTPException(status_code=400, detail="Project already exists in workspace")
    if not os.path.isdir(actual_source_path):
        raise HTTPException(status_code=400, detail="Source folder not found")

    created_project_dir = False
    try:
        os.makedirs(raw_dir, exist_ok=True)
        os.makedirs(processed_dir, exist_ok=True)
        created_project_dir = True  # from here on it is OUR folder and safe to clean up

        new_project = Project(name=project_name)
        db.add(new_project)
        db.commit()
        db.refresh(new_project)

        result = import_selected(db, new_project, auto_selections(actual_source_path), allow_duplicates=True)
        return {"message": "Import successful" + (f" (project folder name: {project_name})" if project_name != (data.get("projectName") or "").strip() else ""),
                "id": new_project.id, "name": project_name, **result}

    except Exception as e:
        db.rollback()
        if created_project_dir and os.path.exists(target_project_path):
            shutil.rmtree(target_project_path, ignore_errors=True)
        leftover = db.query(Project).filter(Project.name == project_name).first()
        if leftover:
            db.delete(leftover)
            db.commit()
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{project_id}")
async def delete_project(project_id: int, db: Session = Depends(get_db)):
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    folder = os.path.abspath(project.workspace_path)
    root = os.path.abspath(WORKSPACE_DIR)
    # never rmtree anything outside the workspace root, whatever the name in the DB says
    if os.path.commonpath([folder, root]) == root and folder != root:
        shutil.rmtree(folder, ignore_errors=True)
    db.delete(project)
    db.commit()
    return {"message": "Project deleted"}

@router.post("/{project_id}/export-pages")
def export_pages(project_id: int, payload: dict, db: Session = Depends(get_db)):
    """
    Export pages: {"path": destination folder ("" / omitted = <project folder>/exported), "chapter_ids": [..] (omit = all
    chapters), "content": "translated" | "clean" | "both", "format": "png" | "jpg", "quality": 50-100, "zip": false,
    "include_untranslated": false, "only_reviewed": false}. Runs as a background job - poll GET /system/jobs/{job_id}.
    """
    from services import backup_service as bs
    from services.render_service import export_chapters, EXPORT_CONTENT, EXPORT_FORMATS
    if not db.query(Project).filter(Project.id == project_id).first():
        raise HTTPException(status_code=404, detail="Project not found")
    dest = (payload.get("path") or "").strip() or None
    content = payload.get("content", "translated")
    fmt = payload.get("format", "png")
    if content not in EXPORT_CONTENT or fmt not in EXPORT_FORMATS:
        raise HTTPException(status_code=400, detail=f"content must be one of {EXPORT_CONTENT}, format one of {EXPORT_FORMATS}")
    try:
        quality = int(payload.get("quality", 92))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="quality must be a number 50-100")
    try:
        job_id = bs.start_job("export_pages", export_chapters, project_id, payload.get("chapter_ids") or None, dest,
                              bool(payload.get("include_untranslated", False)), content, fmt, quality, bool(payload.get("zip", False)),
                              bool(payload.get("only_reviewed", False)))
    except bs.BackupError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {"job_id": job_id}


@router.post("/{project_id}/open-exported")
async def open_exported(project_id: int, db: Session = Depends(get_db)):
    """Open <project folder>/exported in the file explorer (created when nothing was exported yet)."""
    from services.render_service import default_export_root
    from routers.system import open_path, OpenPathRequest
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    folder = default_export_root(project)
    os.makedirs(folder, exist_ok=True)
    await open_path(OpenPathRequest(path=folder))
    return {"folder": folder}


@router.post("/{project_id}/rescan")
def rescan_project(project_id: int, db: Session = Depends(get_db)):
    """
    "Rescan folders" button: compare raw/ + processed/ on disk with the DB right now (new page files, vanished
    files, wrong statuses, new/removed chapter folders) and return exactly what changed or looks wrong.
    """
    from services.reconcile import reconcile_chapters
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    report = reconcile_chapters(db, project_id)
    changed = sum(len(report[k]) for k in ("added", "removed", "fixed"))
    return {"message": f"Rescan done: {changed} change(s), {len(report['warnings'])} warning(s).", **report}


@router.delete("/chapters/{chapter_id}")
async def delete_chapter(chapter_id: int, db: Session = Depends(get_db)):
    """Delete one chapter: DB rows + its raw/ and processed/ folders. (Bulk variant: routers/chapters.py.)"""
    chapter = db.query(Chapter).filter(Chapter.id == chapter_id).first()
    if not chapter:
        raise HTTPException(status_code=404, detail="Chapter not found")
    workspace = chapter.project.workspace_path
    for base in ("raw", "processed"):
        shutil.rmtree(os.path.join(workspace, base, chapter.number), ignore_errors=True)
    db.delete(chapter)
    db.commit()
    return {"message": "Chapter deleted"}

# NOTE: the old POST /{project_id}/import-chapters is removed on purpose. It copied EVERYTHING from the
# chosen folder, numbered chapters with a counter and never checked for duplicates. Use
# POST /projects/{id}/chapters/preview + /chapters/import (routers/chapters.py).
