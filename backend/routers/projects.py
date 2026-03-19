from tkinter import filedialog
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List
import os
import shutil
import tkinter
from models import Project, Chapter, Page
from schemas import ProjectSchema
from database import get_db, WORKSPACE_DIR

# Tworzymy router
router = APIRouter(
    prefix="/projects",
    tags=["Projects"]
)

@router.get("/", response_model=List[ProjectSchema])
async def get_projects(db: Session = Depends(get_db)):
    projects = db.query(Project).all()
    return projects

@router.get("/select-folder")
async def select_folder():
    root = tkinter.Tk()
    root.withdraw()
    root.attributes('-topmost', True)
    folder_path = filedialog.askdirectory(title="Wybierz folder z obrazami")
    root.destroy()
    return {"path": folder_path}


@router.post("/import")
async def import_folder(data: dict, db: Session = Depends(get_db)):
    source_path = data.get("path")
    project_name = data.get("projectName")

    if not source_path or not project_name:
        raise HTTPException(status_code=400, detail="Missing path or project name")

    target_project_path = os.path.join(WORKSPACE_DIR, project_name)

    try:
        if os.path.exists(target_project_path):
            raise HTTPException(status_code=400, detail="Project with this name already exists in workspace")

        shutil.copytree(source_path, target_project_path)

        new_project = Project(name=project_name, workspace_path=target_project_path)
        db.add(new_project)
        db.commit()
        db.refresh(new_project)

        img_extensions = ('.jpg', '.jpeg', '.png', '.webp')
        for root_dir, dirs, files in os.walk(target_project_path):
            pages = sorted([f for f in files if f.lower().endswith(img_extensions)])

            if pages:
                rel_path = os.path.relpath(root_dir, target_project_path)
                chapter_name = "Main" if rel_path == '.' else rel_path.replace('\\', '/')

                new_chapter = Chapter(project_id=new_project.id, number=chapter_name, title=chapter_name)
                db.add(new_chapter)
                db.flush()

                for indx, page_file in enumerate(pages):
                    new_page = Page(chapter_id=new_chapter.id, file_name=page_file, order=indx + 1)
                    db.add(new_page)

        db.commit()
        return {"message": f"Project {project_name} imported successfully!", "id": new_project.id}

    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/cleanup")
async def cleanup_database(db: Session = Depends(get_db)):
    projects = db.query(Project).all()
    removed_count = 0

    for proj in projects:
        if not os.path.exists(proj.workspace_path):
            db.delete(proj)
            removed_count += 1

    db.commit()
    return {"message": f"Database cleared. Deleted {removed_count} not existing projects."}