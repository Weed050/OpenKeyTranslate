
# backend/routers/corrections.py

"""
API Router for the correction-memory system: confirming/persisting a user's
translation for a bubble, browsing the stored corrections for a project (the
"TM analytics" / memory browser panel), and deleting stale entries.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from core.database import get_db
from models.models import Page, Project, Correction
from models.schemas import CorrectionSchema
from services.memory_service import save_correction, delete_correction, encode_embedding, embed_text, get_correction_usage
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
        return {"message": "Saved (not added to memory: unchanged, duplicate, or too short)", "correction_id": None}
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

@router.get("/{correction_id}/usage")
async def correction_usage(correction_id: int, db: Session = Depends(get_db)):
    """Every logged translation where this correction was injected as the hint - for the memory browser's expandable usage panel."""
    correction = db.query(Correction).filter(Correction.id == correction_id).first()
    if not correction:
        raise HTTPException(status_code=404, detail="Correction not found")

    logs = get_correction_usage(correction_id, db)
    return [
        {
            "source_text": log.source_text, "output_text": log.output_text,
            "similarity_score": log.similarity_score, "page_id": log.page_id,
            "created_at": log.created_at.isoformat() if log.created_at else None,
        }
        for log in logs
    ]


@router.get("/export/{project_id}")
async def export_corrections(project_id: int, db: Session = Depends(get_db)):
    """Dump every correction for a project as plain JSON (embeddings not exported - regenerated on import)."""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    corrections = db.query(Correction).filter(Correction.project_id == project_id).all()
    return {
        "project_name": project.name,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "corrections": [
            {
                "source_text": c.source_text,
                "ai_translation": c.ai_translation,
                "final_translation": c.final_translation,
                "reuse_count": c.reuse_count,
                "created_at": c.created_at.isoformat() if c.created_at else None,
            }
            for c in corrections
        ],
    }


@router.post("/import/{project_id}")
async def import_corrections(project_id: int, payload: dict, db: Session = Depends(get_db)):
    """
    Re-import an exported correction set. Re-embeds locally (embeddings
    aren't portable across model versions). Skips (source_text,
    final_translation) pairs that already exist - safe to re-run.
    """
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    existing = {
        (c.source_text, c.final_translation.strip().lower())
        for c in db.query(Correction).filter(Correction.project_id == project_id).all()
    }

    added = 0
    for row in payload.get("corrections", []):
        final_translation = (row.get("final_translation") or "").strip()
        source_text = row.get("source_text") or ""
        if not source_text or not final_translation:
            continue
        key = (source_text, final_translation.lower())
        if key in existing:
            continue

        correction = Correction(
            project_id=project_id,
            source_text=source_text,
            ai_translation=row.get("ai_translation"),
            final_translation=final_translation,
            reuse_count=row.get("reuse_count", 0),
            embedding=encode_embedding(embed_text(source_text)),
        )
        db.add(correction)
        existing.add(key)
        added += 1

    db.commit()
    return {"message": f"Imported {added} correction(s).", "added": added}
