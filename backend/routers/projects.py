
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
    Copies images, normalizes filenames, and creates corresponding database entries.
    """

    actual_source_path = data.get("path")
    project_name = data.get("projectName")

    if not actual_source_path or not project_name:
        raise HTTPException(status_code=400, detail="Missing path or project name")

    target_project_path = os.path.join(WORKSPACE_DIR, project_name)
    raw_dir = os.path.join(target_project_path, "raw")
    processed_dir = os.path.join(target_project_path, "processed")

    try:
        if os.path.exists(target_project_path):
            raise HTTPException(status_code=400, detail="Project already exists in workspace")

        os.makedirs(raw_dir, exist_ok=True)
        os.makedirs(processed_dir, exist_ok=True)

        new_project = Project(name=project_name, workspace_path=target_project_path)
        db.add(new_project)
        db.commit()
        db.refresh(new_project)

        img_extensions = ('.jpg', '.jpeg', '.png', '.webp')
        chapter_counter = 0

        for root_dir, dirs, files in os.walk(actual_source_path):

            dirs.sort()

            pages = sorted([f for f in files if f.lower().endswith(img_extensions)])

            if pages:
                # Define the chapter label based on the counter
                chapter_label = f"Chapter_{chapter_counter}"

                chapter_raw_dir = os.path.join(raw_dir, chapter_label)
                chapter_processed_dir = os.path.join(processed_dir, chapter_label)

                os.makedirs(chapter_raw_dir, exist_ok=True)
                os.makedirs(chapter_processed_dir, exist_ok=True)

                # Use chapter_label as the chapter chapter_name
                new_chapter = Chapter(
                    project_id=new_project.id,
                    number=chapter_label,  # e.g., Chapter_0
                    title=f"Chapter {chapter_counter}",
                    raw_path=chapter_raw_dir,
                    processed_path=chapter_processed_dir
                )
                db.add(new_chapter)
                db.flush()

                for indx, page_file in enumerate(pages):
                    ext = os.path.splitext(page_file)[1].lower()
                    standard_filename = f"page_{indx + 1:03d}{ext}"

                    src_file_path = os.path.join(root_dir, page_file)
                    dest_file_path = os.path.join(chapter_raw_dir, standard_filename)

                    shutil.copy2(src_file_path, dest_file_path)

                    new_page = Page(chapter_id=new_chapter.id, file_name=standard_filename, order=indx + 1)
                    db.add(new_page)

                chapter_counter += 1

        db.commit()
        return {"message": "Import successful", "id": new_project.id}

    except Exception as e:
        db.rollback()
        if 'target_project_path' in locals() and os.path.exists(target_project_path):
            shutil.rmtree(target_project_path)
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

    for p in db.query(Project).all():
        if not os.path.isdir(p.workspace_path):
            print(f"[RECONCILE] Removing DB entry for missing folder: {p.name}")
            db.delete(p)

    db.commit()


@router.post("/{project_id}/import-chapters")
async def import_additional_chapters(project_id: int, data: dict, db: Session = Depends(get_db)):
    """Add new chapters to an existing project from a folder on disk. Doesn't touch existing chapters."""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    actual_source_path = data.get("path")
    if not actual_source_path or not os.path.isdir(actual_source_path):
        raise HTTPException(status_code=400, detail="Missing or invalid source path")

    raw_dir = os.path.join(project.workspace_path, "raw")
    processed_dir = os.path.join(project.workspace_path, "processed")
    existing_numbers = {c.number for c in project.chapters}
    existing_indices = [int(m.group(1)) for c in existing_numbers if (m := re.match(r"Chapter_(\d+)", c))]
    chapter_counter = max(existing_indices, default=-1) + 1

    img_extensions = ('.jpg', '.jpeg', '.png', '.webp', '.jfif', '.bmp', '.tif', '.tiff')  # .gif/.avif - risky - OpenCV (cv2.imdecode) in pages.py/ocr_pipeline.py cannot handle these properly
    added_chapters = added_pages = 0
    skipped_folders = []

    for root_dir, dirs, files in os.walk(actual_source_path):
        dirs.sort(key=natural_sort_key)

        candidate_files = [f for f in files if f.lower().endswith(img_extensions)]

        if not candidate_files and files:
            # Folder contains files, but none match the supported formats
            unsupported = sorted({os.path.splitext(f)[1].lower() for f in files if os.path.splitext(f)[1]})
            skipped_folders.append({
                "folder": os.path.relpath(root_dir, actual_source_path) or ".",
                "extensions_found": unsupported,
            })
            continue

        pages = sorted(
            candidate_files,
            key=lambda f: (os.path.getmtime(os.path.join(root_dir, f)), natural_sort_key(f))
        )
        if not pages:
            continue

        chapter_label = f"Chapter_{chapter_counter}"
        chapter_raw_dir = os.path.join(raw_dir, chapter_label)
        chapter_processed_dir = os.path.join(processed_dir, chapter_label)
        os.makedirs(chapter_raw_dir, exist_ok=True)
        os.makedirs(chapter_processed_dir, exist_ok=True)

        new_chapter = Chapter(
            project_id=project.id,
            number=chapter_label,
            title=f"Chapter {chapter_counter}",
            raw_path=chapter_raw_dir,
            processed_path=chapter_processed_dir,
        )
        db.add(new_chapter)
        db.flush()

        for indx, page_file in enumerate(pages):
            ext = os.path.splitext(page_file)[1].lower()
            standard_filename = f"page_{indx + 1:03d}{ext}"
            src_file_path = os.path.join(root_dir, page_file)
            dest_file_path = os.path.join(chapter_raw_dir, standard_filename)
            shutil.copy2(src_file_path, dest_file_path)
            new_page = Page(chapter_id=new_chapter.id, file_name=standard_filename, order=indx + 1)
            db.add(new_page)
            added_pages += 1

        chapter_counter += 1
        added_chapters += 1

    db.commit()

    message = f"Added {added_chapters} chapter(s), {added_pages} page(s)."
    if skipped_folders:
        exts = sorted({e for sf in skipped_folders for e in sf["extensions_found"]})
        lines = [f"- {sf['folder']} ({', '.join(sf['extensions_found']) or 'no extension'})" for sf in
                 skipped_folders]
        message += (
                f"\n\nSkipped {len(skipped_folders)} folder(s) — unsupported format: {', '.join(exts)}.\n"
                + "\n".join(lines)
                + f"\n\nSupported formats: {', '.join(img_extensions)}"
        )

    return {
        "message": message,
        "chapters_added": added_chapters,
        "pages_added": added_pages,
        "skipped_folders": skipped_folders,
    }