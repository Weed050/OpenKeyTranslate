# backend/routers/corrections.py

"""
API Router for confirming and persisting user translation corrections.

This is the write path for the project's correction-memory system: whenever
the user edits or approves a bubble's translation in the frontend editor,
this endpoint stores it (see services/memory_service.py) so it can be
retrieved as a stylistic hint the next time similar text is translated.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from core.database import get_db
from models.models import Page
from models.schemas import CorrectionSchema
from services.memory_service import save_correction

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

    Called by the frontend editor whenever the user approves a bubble as-is
    or edits its text - both count as a "correction" for memory purposes,
    since even an unedited approval confirms the AI's proposal was correct.

    `bubble_id` identifies the source bubble for traceability/logging, but is
    not itself stored on Correction - matching later is done purely by
    source-text embedding similarity, not by original bubble identity.
    """
    page = db.query(Page).filter(Page.id == page_id).first()
    if not page:
        raise HTTPException(status_code=404, detail="Page not found")

    project_id = page.chapter.project_id

    correction = save_correction(
        db=db,
        project_id=project_id,
        source_text=payload.source_text,
        ai_translation=payload.ai_translation,
        final_translation=payload.final_translation,
    )

    return {"message": "Correction saved", "correction_id": correction.id}