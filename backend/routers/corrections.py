
# backend/routers/corrections.py

"""
API Router for the correction-memory system: confirming/persisting a user's
translation for a bubble, browsing the stored corrections for a project (the
"TM analytics" / memory browser panel), and deleting stale entries.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from core.database import get_db
from models.models import Page, Project, Correction
from models.schemas import CorrectionSchema
from services.memory_service import save_correction, delete_correction
from services.page_export import page_paths, update_bubble_translation

router = APIRouter(prefix="/corrections", tags=["Corrections"])


@router.post("/pages/{page_id}/bubbles/{bubble_id}")
async def confirm_bubble_correction(
    page_id: int,
    bubble_id: str,
    payload: CorrectionSchema,
    db: Session = Depends(get_db),
):
    """
    Persist a user-confirmed translation for a single bubble.

    Two things happen, independently:
    1. The page's own saved JSON is updated so `translation` reflects what
       the user just typed (see services/page_export.update_bubble_translation) -
       this always happens, so reopening the editor shows the edit.
    2. save_correction() decides separately whether this is *also* worth
       adding to correction memory (see services/memory_service.py's delta
       and junk gates) - an unedited acceptance or a too-short fragment
       still updates (1) but is skipped for (2), returning correction_id: null.
    """
    page = db.query(Page).filter(Page.id == page_id).first()
    if not page:
        raise HTTPException(status_code=404, detail="Page not found")

    project_id = page.chapter.project_id
    paths = page_paths(page)
    update_bubble_translation(paths["json"], bubble_id, payload.final_translation)

    correction = save_correction(
        db=db,
        project_id=project_id,
        source_text=payload.source_text,
        ai_translation=payload.ai_translation,
        final_translation=payload.final_translation,
    )

    if correction is None:
        return {"message": "Saved (not added to memory: unchanged or too short)", "correction_id": None}
    return {"message": "Correction saved", "correction_id": correction.id}


@router.get("/by-project/{project_id}")
async def list_corrections(project_id: int, db: Session = Depends(get_db)):
    """List every stored correction for a project, for the TM/memory browser panel."""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    corrections = (
        db.query(Correction)
        .filter(Correction.project_id == project_id)
        .order_by(Correction.created_at.desc())
        .all()
    )

    return [
        {
            "id": c.id,
            "source_text": c.source_text,
            "ai_translation": c.ai_translation,
            "final_translation": c.final_translation,
            "reuse_count": c.reuse_count,
            "created_at": c.created_at.isoformat() if c.created_at else None,
        }
        for c in corrections
    ]


@router.delete("/{correction_id}")
async def remove_correction(correction_id: int, db: Session = Depends(get_db)):
    """Delete a stale or mistaken correction so it stops being suggested as a hint."""
    deleted = delete_correction(db, correction_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Correction not found")
    return {"message": "Correction deleted"}
