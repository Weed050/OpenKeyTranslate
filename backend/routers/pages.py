
# backend/routers/pages.py

"""
API Router for running the OCR/translation pipeline on a page, reading its
resulting bubble data, and serving the inpainted page image to the frontend
editor.

The /process endpoint is the one-click equivalent of test_main.py's
test_ocr_from_db(): same pipeline (process_image -> translate_bubbles ->
erase_text_from_image -> save_ocr_json), just triggered from the browser
instead of a second terminal. It is synchronous - for a single manga page
this typically finishes in seconds to a couple of minutes depending on page
size and the active translation provider, which is acceptable for a
single-user local tool. Revisit with a background task queue only if this
becomes an actual bottleneck.

CAVEAT: box coordinates in the saved JSON are in the OCR pipeline's *scaled*
pixel space (saved before to_original_coords is ever applied). They only
line up 1:1 with the inpainted image served below when ocr_scale == 1.0
(today's default in core/config.py).
"""

import os
import json
import cv2
import numpy as np
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from core.database import get_db
from core.config import TRANSLATION_ON
from models.models import Project, Page
from services.ocr_pipeline import process_image, to_original_coords
from services.translation_service import translate_bubbles
from services.page_export import save_ocr_json, page_paths
from utils.inpainting import erase_text_from_image

router = APIRouter(prefix="/pages", tags=["Pages"])


@router.get("/by-project/{project_id}")
async def list_pages(project_id: int, db: Session = Depends(get_db)):
    """List every page across all chapters of a project, for the page picker / chapter nav."""
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
    """Return bubble data (source text, current + AI-original translation, box) for one page."""
    page = db.query(Page).filter(Page.id == page_id).first()
    if not page:
        raise HTTPException(status_code=404, detail="Page not found")

    paths = page_paths(page)
    if not os.path.exists(paths["json"]):
        raise HTTPException(status_code=404, detail="Page has not been OCR-processed yet")

    with open(paths["json"], "r", encoding="utf-8") as f:
        ocr_data = json.load(f)

    return {
        "page_id": page.id,
        "project_id": page.chapter.project_id,
        "bubbles": ocr_data.get("bubbles", []),
        "json_path": paths["json"],
    }


@router.get("/{page_id}/image")
async def get_page_image(page_id: int, db: Session = Depends(get_db)):
    """Serve the inpainted (clean, text-erased) page image the editor overlays text on."""
    page = db.query(Page).filter(Page.id == page_id).first()
    if not page:
        raise HTTPException(status_code=404, detail="Page not found")

    paths = page_paths(page)
    if not os.path.exists(paths["image"]):
        raise HTTPException(status_code=404, detail="Inpainted image not found - run the pipeline first")

    return FileResponse(paths["image"], media_type="image/png")


@router.delete("/{page_id}/bubbles/{bubble_id}")
async def delete_bubble(page_id: int, bubble_id: str, db: Session = Depends(get_db)):
    """
    Remove a single bubble from this page's saved OCR/translation data - for
    OCR false positives (a "bubble" detected where there's no real text).
    Does not touch correction memory; only the page's own bubble list.
    """
    page = db.query(Page).filter(Page.id == page_id).first()
    if not page:
        raise HTTPException(status_code=404, detail="Page not found")

    paths = page_paths(page)
    if not os.path.exists(paths["json"]):
        raise HTTPException(status_code=404, detail="Page has not been OCR-processed yet")

    with open(paths["json"], "r", encoding="utf-8") as f:
        ocr_data = json.load(f)

    before = len(ocr_data.get("bubbles", []))
    ocr_data["bubbles"] = [b for b in ocr_data.get("bubbles", []) if b["bubble_id"] != bubble_id]

    if len(ocr_data["bubbles"]) == before:
        raise HTTPException(status_code=404, detail="Bubble not found on this page")

    with open(paths["json"], "w", encoding="utf-8") as f:
        json.dump(ocr_data, f, ensure_ascii=False, indent=2)

    return {"message": "Bubble deleted", "remaining": len(ocr_data["bubbles"])}


@router.post("/{page_id}/retranslate")
def retranslate_page(page_id: int, db: Session = Depends(get_db)):
    """
    Re-run translation only, reusing the OCR text/boxes already saved in
    this page's JSON - no OCR or inpainting re-run. For retrying a page
    where the AI failed or returned empty translations for some bubbles,
    or after adding more correction-memory data, without burning GPU time
    re-running OCR that already succeeded.

    Overwrites each bubble's `translation` AND `ai_translation` with the
    fresh result - this retranslation *is* the new AI baseline going
    forward, so "Reset to AI" in the editor reverts to this, not the
    original /process output.
    """
    page = db.query(Page).filter(Page.id == page_id).first()
    if not page:
        raise HTTPException(status_code=404, detail="Page not found")

    paths = page_paths(page)
    if not os.path.exists(paths["json"]):
        raise HTTPException(status_code=404, detail="Page has not been OCR-processed yet")

    with open(paths["json"], "r", encoding="utf-8") as f:
        ocr_data = json.load(f)

    # translate_bubbles() only reads 'bubble_id' and 'text' from each entry.
    bubbles = [
        {"bubble_id": b["bubble_id"], "text": b["text"]}
        for b in ocr_data.get("bubbles", [])
    ]
    if not bubbles:
        raise HTTPException(status_code=422, detail="No bubbles found on this page to translate")

    try:
        translated = translate_bubbles(
            bubbles, project_id=page.chapter.project_id, page_id=page.id, db=db
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Translation failed: {e}")

    translation_by_id = {b["bubble_id"]: b["translation"] for b in translated}
    for b in ocr_data["bubbles"]:
        if b["bubble_id"] in translation_by_id:
            new_translation = translation_by_id[b["bubble_id"]]
            b["translation"] = new_translation
            b["ai_translation"] = new_translation

    with open(paths["json"], "w", encoding="utf-8") as f:
        json.dump(ocr_data, f, ensure_ascii=False, indent=2)

    empty_count = sum(1 for t in translation_by_id.values() if not t.strip())
    return {
        "message": "Retranslated" + (f" ({empty_count} bubble(s) still came back empty)" if empty_count else ""),
        "bubble_count": len(bubbles),
        "empty_count": empty_count,
    }


@router.post("/{page_id}/process")
def process_page(page_id: int, db: Session = Depends(get_db)):
    """
    Run OCR -> translation -> inpainting for one page and persist the
    results, so GET /pages/{id} and GET /pages/{id}/image work right after.

    Defined as a plain `def` (not `async def`) so FastAPI runs it in a
    worker thread instead of blocking the event loop for the whole
    (potentially long) pipeline run.
    """
    page = db.query(Page).filter(Page.id == page_id).first()
    if not page:
        raise HTTPException(status_code=404, detail="Page not found")

    paths = page_paths(page)

    if not os.path.exists(paths["raw"]):
        raise HTTPException(status_code=404, detail=f"Raw image not found at {paths['raw']}")

    image = cv2.imdecode(np.fromfile(paths["raw"], np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise HTTPException(status_code=422, detail=f"Could not decode image at {paths['raw']}")

    if len(image.shape) == 3 and image.shape[2] == 4:
        image = cv2.cvtColor(image, cv2.COLOR_BGRA2BGR)

    try:
        ocr_result = process_image(image)

        if TRANSLATION_ON:
            ocr_result["bubbles"] = translate_bubbles(
                ocr_result["bubbles"],
                project_id=page.chapter.project_id,
                page_id=page.id,
                db=db,
            )

        os.makedirs(paths["out_dir"], exist_ok=True)
        save_ocr_json(paths["out_dir"], paths["file_base_name"], ocr_result)

        inpainted_image = erase_text_from_image(image, ocr_result["lines"], to_original_coords)
        cv2.imwrite(paths["image"], inpainted_image)

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Pipeline failed: {e}")

    page.status = "processed"
    db.commit()

    return {
        "message": "Page processed",
        "page_id": page.id,
        "bubble_count": len(ocr_result["bubbles"]),
    }
