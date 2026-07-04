
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

def natural_sort_key(s):
    """
        Sorting key that correctly handles numbers embedded in strings.
        Ensures that '2_image.jpg' comes before '10_image.jpg' rather than after it.
        """
    return [int(text) if text.isdigit() else text.lower() for text in re.split(r'(\d+)', s)]