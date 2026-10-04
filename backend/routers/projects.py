
# backend/routers/projects.py

"""
API Router used for managing projects and file imports.

Handles fetching project lists, native directory selection via Tkinter,
batch importing images, and synchronizing the database with the filesystem.
"""

import re
from tkinter import filedialog
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List
import os
import shutil
import tkinter
from models.models import Project, Chapter, Page
from models.schemas import ProjectSchema
from core.database import get_db
from core.config import WORKSPACE_DIR
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
    project_name = (data.get("projectName") or "").strip()

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

        new_project = Project(name=project_name, workspace_path=target_project_path)
        db.add(new_project)
        db.commit()
        db.refresh(new_project)

        result = import_selected(db, new_project, auto_selections(actual_source_path), allow_duplicates=True)
        return {"message": "Import successful", "id": new_project.id, **result}

    except Exception as e:
        db.rollback()
        if created_project_dir and os.path.exists(target_project_path):
            shutil.rmtree(target_project_path, ignore_errors=True)
        leftover = db.query(Project).filter(Project.name == project_name).first()
        if leftover:
            db.delete(leftover)
            db.commit()
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/cleanup")
async def cleanup_database(db: Session = Depends(get_db)):
    """
    Synchronize the database with the filesystem.
    Removes database entries for projects whose physical workspace directories no longer exist.
    """

    projects = db.query(Project).all()
    removed_count = 0

    for proj in projects:
        if not os.path.exists(proj.workspace_path):
            db.delete(proj)
            removed_count += 1

    db.commit()
    return {"message": f"Database cleared. Deleted {removed_count} not existing projects."}

@router.delete("/{project_id}")
async def delete_project(project_id: int, db: Session = Depends(get_db)):
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    shutil.rmtree(project.workspace_path, ignore_errors=True)
    db.delete(project)
    db.commit()
    return {"message": "Project deleted"}

def natural_sort_key(s):
    """
        Sorting key that correctly handles numbers embedded in strings.
        Ensures that '2_image.jpg' comes before '10_image.jpg' rather than after it.
        """
    return [int(text) if text.isdigit() else text.lower() for text in re.split(r'(\d+)', s)]



def reconcile_projects_from_disk(db: Session):
    """
    Startup safety net: project folders on disk with no matching DB row
    (DB deleted/replaced but files survived) get re-registered so they
    reappear in the sidebar. Doesn't touch projects already in the DB.
    """

    if not os.path.isdir(WORKSPACE_DIR):
        return

    # 0. Heal moved workspaces: the DB keeps ABSOLUTE paths. If a project's folder is gone but
    #    <WORKSPACE_DIR>/<name> exists (workspace moved/copied by hand, drive letter changed), repoint the row
    #    instead of treating the folder as a NEW project (UNIQUE name -> IntegrityError -> app fails to start)
    #    and deleting the "missing" one below together with its corrections and glossary.
    for p in db.query(Project).all():
        candidate = os.path.join(WORKSPACE_DIR, p.name)
        if not os.path.isdir(p.workspace_path) and os.path.isdir(candidate):
            print(f"[RECONCILE] project '{p.name}': path {p.workspace_path} -> {candidate}")
            for ch in p.chapters:
                ch.raw_path = os.path.join(candidate, "raw", ch.number)
                ch.processed_path = os.path.join(candidate, "processed", ch.number)
            p.workspace_path = candidate
    db.flush()

    known_paths = {p.workspace_path for p in db.query(Project).all()}

    for entry in sorted(os.listdir(WORKSPACE_DIR)):
        project_path = os.path.join(WORKSPACE_DIR, entry)
        raw_dir = os.path.join(project_path, "raw")
        if not os.path.isdir(raw_dir) or project_path in known_paths:
            continue

        print(f"[RECONCILE] Re-registering orphaned project folder: {entry}")
        project = Project(name=entry, workspace_path=project_path)
        db.add(project)
        db.flush()

        img_extensions = ('.jpg', '.jpeg', '.png', '.webp', '.jfif')
        for chapter_label in sorted(os.listdir(raw_dir)):
            chapter_raw_dir = os.path.join(raw_dir, chapter_label)
            if not os.path.isdir(chapter_raw_dir):
                continue

            chapter = Chapter(
                project_id=project.id, number=chapter_label, title=chapter_label,
                raw_path=chapter_raw_dir,
                processed_path=os.path.join(project_path, "processed", chapter_label),
            )
            db.add(chapter)
            db.flush()

            pages = sorted(f for f in os.listdir(chapter_raw_dir) if f.lower().endswith(img_extensions))
            for indx, page_file in enumerate(pages):
                file_base = os.path.splitext(page_file)[0]
                json_path = os.path.join(project_path, "processed", chapter_label, f"{file_base}_ocr.json")
                status = "processed" if os.path.exists(json_path) else "pending"
                db.add(Page(chapter_id=chapter.id, file_name=page_file, order=indx + 1, status=status))

    all_projects = db.query(Project).all()
    missing = [p for p in all_projects if not os.path.isdir(p.workspace_path)]
    if all_projects and len(missing) == len(all_projects):
        # EVERY project folder is gone: offline drive / wrong workspace path, not "the user deleted everything".
        # Deleting here would cascade-delete all corrections + glossary (the thesis data). Keep the rows, warn.
        print(f"[RECONCILE] WARNING: none of the {len(all_projects)} project folders exist (drive offline / wrong "
              f"workspace path?). DB rows kept.")
    else:
        for p in missing:
            print(f"[RECONCILE] Removing DB entry for missing folder: {p.name}")
            db.delete(p)

    db.commit()


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
