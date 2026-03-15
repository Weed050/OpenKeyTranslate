from typing import List
import os
from fastapi import FastAPI, Depends, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from schemas import UploadResponse, OcrText, ProjectSchema
from sqlalchemy.orm import Session
import shutil
from models import Project, Chapter, Page, TextBlock
import uvicorn
from database import get_db, init_db
import tkinter
from tkinter import filedialog
from paddleocr import PaddleOCR


@asynccontextmanager
async def lifespan(app: FastAPI):
    print("Database inicjalization ...")
    init_db()
    print("Database ready.")
    yield

    print("Closing session ...")

app = FastAPI(lifespan=lifespan)

# Konfiguracja cors - fontent:backend - middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # adres frontendu
    allow_methods = ["*"],
    allow_headers = ["*"],
)

@app.get("/projects", response_model=List[ProjectSchema])
async def get_projects(db: Session = Depends(get_db)):
    projects = db.query(Project).all()
    return projects

ocr_model = PaddleOCR(use_textline_orientation=True, lang='en')

@app.get("/select-folder")
async def select_folder():
    root = tkinter.Tk()
    root.withdraw()
    root.attributes('-topmost', True)

    directory = filedialog.askdirectory()
    root.destroy()

    if not directory:
        return {"path": None}
    return {"path": directory}


@app.post("/import-folder", response_model=UploadResponse)
async def import_folder(data: dict, db: Session = Depends(get_db)):
    source_path = data.get("path")
    project_name = data.get("project_name")

    if not source_path or not project_name:
        return {"message": "Error: missing path or project name"}

    new_project = Project(name=project_name, workspace_path=source_path)
    db.add(new_project)
    db.commit()
    db.refresh(new_project)

    img_extensions = ('.jpg', '.jpeg', '.png', '.webp')

    try:
        for root_dir, dirs, files in os.walk(source_path):
            pages = sorted([f for f in files if f.lower().endswith(img_extensions)])

            if pages:
                rel_path = os.path.relpath(root_dir, source_path)
                chapter_name = "Main" if rel_path == '.' else rel_path.replace('\\', '/')

                new_chapter = Chapter(
                    project_id=new_project.id,
                    number=chapter_name,
                    title=chapter_name
                )
                db.add(new_chapter)
                db.flush()

                for indx, page_file in enumerate(pages):
                    new_page = Page(
                        chapter_id=new_chapter.id,
                        file_name=page_file,
                        order=indx + 1
                    )
                    db.add(new_page)

        db.commit()
        return {"message": f"Project {project_name} imported successfully!", "id": new_project.id}

    except Exception as e:
        db.rollback()
        return {"message": f"Error during scanning: {str(e)}"}

if __name__ == '__main__':
    uvicorn.run(app, host="127.0.0.1", port=8000)