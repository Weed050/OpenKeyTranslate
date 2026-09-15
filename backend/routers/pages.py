# backend/routers/pages.py

"""
API Router for reading page-level OCR/translation data and serving the
inpainted page image to the frontend editor.

Bubble data is read from the JSON file already written by the OCR pipeline
(see the save_ocr_json helper in test_main.py) - there is no separate DB
write path for bubble content yet, so this router treats that JSON file as
the source of truth for "what's on this page".

CAVEAT: box coordinates in that JSON are in the OCR pipeline's *scaled*
pixel space (saved before to_original_coords is ever applied - see
test_main.py). They only line up 1:1 with the inpainted image served below
when ocr_scale == 1.0 (today's default in core/config.py). If ocr_scale is
ever changed, these coordinates will need dividing by SCALE before use -
not yet done anywhere in the export path.
"""

import os
import json
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from core.database import get_db
from models.models import Project, Page

router = APIRouter(prefix="/pages", tags=["Pages"])


def _page_paths(page: Page) -> dict:
    """Resolve the on-disk JSON and inpainted-image paths for a given page."""
    chapter = page.chapter
    project = chapter.project
    file_base_name = os.path.splitext(page.file_name)[0]
    out_dir = os.path.join(project.workspace_path, "processed", chapter.number)
    return {
        "json": os.path.join(out_dir, f"{file_base_name}_ocr.json"),
        "image": os.path.join(out_dir, f"ocr_{file_base_name}_inpainted.png"),
    }


@router.get("/by-project/{project_id}")
async def list_pages(project_id: int, db: Session = Depends(get_db)):
    """List every page across all chapters of a project, for the page picker."""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    return [
        {
            "page_id": page.id,
            "chapter": chapter.number,
            "file_name": page.file_name,
            "order": page.order,
            "status": page.status,
        }
        for chapter in project.chapters
        for page in chapter.pages
    ]


@router.get("/{page_id}")
async def get_page(page_id: int, db: Session = Depends(get_db)):
    """Return bubble data (source text, current translation, box) for one page."""
    page = db.query(Page).filter(Page.id == page_id).first()
    if not page:
        raise HTTPException(status_code=404, detail="Page not found")

    paths = _page_paths(page)
    if not os.path.exists(paths["json"]):
        raise HTTPException(status_code=404, detail="Page has not been OCR-processed yet")

    with open(paths["json"], "r", encoding="utf-8") as f:
        ocr_data = json.load(f)

    return {
        "page_id": page.id,
        "project_id": page.chapter.project_id,
        "bubbles": ocr_data.get("bubbles", []),
    }


@router.get("/{page_id}/image")
async def get_page_image(page_id: int, db: Session = Depends(get_db)):
    """Serve the inpainted (clean, text-erased) page image the editor overlays text on."""
    page = db.query(Page).filter(Page.id == page_id).first()
    if not page:
        raise HTTPException(status_code=404, detail="Page not found")

    paths = _page_paths(page)
    if not os.path.exists(paths["image"]):
        raise HTTPException(status_code=404, detail="Inpainted image not found - run the pipeline first")

    return FileResponse(paths["image"], media_type="image/png")
