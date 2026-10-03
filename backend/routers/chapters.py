
# backend/routers/chapters.py

"""
Chapter-level endpoints: import preview, guarded import, bulk delete.

  POST /projects/{project_id}/chapters/preview  {path}
        -> what WOULD be imported (label, pages, new/duplicate/label_conflict)
  POST /projects/{project_id}/chapters/import   {chapters:[{folder,label}], allow_duplicates?}
        -> imports exactly the chosen folders under the chosen labels (409 + errors on conflict)
  POST /projects/chapters/delete-many           {chapter_ids:[...]}
        -> removes chapters (rows, pages, raw/ + processed/ folders) in one call

The old blind POST /projects/{id}/import-chapters is gone (see routers/projects.py).
TranslationLog rows of deleted pages are kept (thesis data) - they simply stop showing up in
the Logs page, which joins through Page.
"""

import os
import shutil

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from core.database import get_db
from models.models import Chapter, Project
from services.chapter_import import ChapterImportError, build_preview, import_selected

router = APIRouter(prefix="/projects", tags=["Chapters"])


class PreviewRequest(BaseModel):
    path: str


class ChapterChoice(BaseModel):
    folder: str
    label: str


class ImportRequest(BaseModel):
    chapters: list[ChapterChoice]
    allow_duplicates: bool = False


class DeleteManyRequest(BaseModel):
    chapter_ids: list[int]


def _project_or_404(db: Session, project_id: int) -> Project:
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


@router.post("/{project_id}/chapters/preview")
def preview_chapters(project_id: int, payload: PreviewRequest, db: Session = Depends(get_db)):
    project = _project_or_404(db, project_id)
    if not payload.path or not os.path.isdir(payload.path):
        raise HTTPException(status_code=400, detail="Missing or invalid source path")
    return build_preview(project, payload.path)


@router.post("/{project_id}/chapters/import")
def import_chapters(project_id: int, payload: ImportRequest, db: Session = Depends(get_db)):
    project = _project_or_404(db, project_id)
    if not payload.chapters:
        raise HTTPException(status_code=400, detail="Nothing selected")
    try:
        result = import_selected(
            db, project, [c.model_dump() for c in payload.chapters], allow_duplicates=payload.allow_duplicates
        )
    except ChapterImportError as e:
        raise HTTPException(status_code=409, detail="Import blocked:\n- " + "\n- ".join(e.errors))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Import failed (rolled back): {e}")
    return {"message": f"Added {result['chapters_added']} chapter(s), {result['pages_added']} page(s).", **result}


@router.post("/chapters/delete-many")
def delete_many_chapters(payload: DeleteManyRequest, db: Session = Depends(get_db)):
    chapters = db.query(Chapter).filter(Chapter.id.in_(payload.chapter_ids)).all()
    if not chapters:
        raise HTTPException(status_code=404, detail="No matching chapters")

    deleted, pages_removed = [], 0
    for ch in chapters:
        project = ch.project
        pages_removed += len(ch.pages)
        for base in ("raw", "processed"):
            shutil.rmtree(os.path.join(project.workspace_path, base, ch.number), ignore_errors=True)
        deleted.append(ch.number)
        db.delete(ch)  # cascades to pages
    db.commit()
    return {"message": f"Deleted {len(deleted)} chapter(s), {pages_removed} page(s).", "deleted": deleted}