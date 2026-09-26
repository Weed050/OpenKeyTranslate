
# backend/routers/glossary.py

"""
API Router for the deterministic term-base (glossary): CRUD on fixed
English->Polish term pairs, plus a suggestion endpoint that mines
existing Corrections for recurring patterns (see services/glossary_service.py).
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from core.database import get_db
from models.models import Project, GlossaryTerm
from datetime import datetime, timezone
from services.glossary_service import suggest_glossary_terms, get_term_usage

router = APIRouter(prefix="/glossary", tags=["Glossary"])


@router.get("/by-project/{project_id}")
async def list_terms(project_id: int, db: Session = Depends(get_db)):
    """List every glossary term for a project."""
    terms = (
        db.query(GlossaryTerm)
        .filter(GlossaryTerm.project_id == project_id)
        .order_by(GlossaryTerm.source_term)
        .all()
    )
    return [
        {"id": t.id, "source_term": t.source_term, "target_term": t.target_term,
         "auto_detected": t.auto_detected, "occurrences": t.occurrences}
        for t in terms
    ]


@router.post("/by-project/{project_id}")
async def add_term(project_id: int, payload: dict, db: Session = Depends(get_db)):
    """Add a new glossary term (manual entry, or confirming a suggestion)."""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    source_term = (payload.get("source_term") or "").strip()
    target_term = (payload.get("target_term") or "").strip()
    if not source_term or not target_term:
        raise HTTPException(status_code=400, detail="source_term and target_term are required")

    term = GlossaryTerm(
        project_id=project_id, source_term=source_term, target_term=target_term,
        auto_detected=payload.get("auto_detected", False), occurrences=payload.get("occurrences", 1),
    )
    db.add(term)
    db.commit()
    db.refresh(term)
    return {"message": "Term added", "id": term.id}


@router.delete("/{term_id}")
async def delete_term(term_id: int, db: Session = Depends(get_db)):
    """Remove a glossary term - it stops being injected into future translations."""
    term = db.query(GlossaryTerm).filter(GlossaryTerm.id == term_id).first()
    if not term:
        raise HTTPException(status_code=404, detail="Term not found")
    db.delete(term)
    db.commit()
    return {"message": "Term deleted"}


@router.get("/suggestions/{project_id}")
async def get_suggestions(project_id: int, min_occurrences: int = 2, db: Session = Depends(get_db)):
    """Mine existing Corrections for recurring term-level fixes the user could promote to the glossary."""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return suggest_glossary_terms(project_id, db, min_occurrences)

@router.get("/{term_id}/usage")
async def term_usage(term_id: int, db: Session = Depends(get_db)):
    """Every logged translation where this glossary term actually fired - for the glossary panel's expandable usage view."""
    term = db.query(GlossaryTerm).filter(GlossaryTerm.id == term_id).first()
    if not term:
        raise HTTPException(status_code=404, detail="Term not found")
    return get_term_usage(term, term.project_id, db)


@router.get("/export/{project_id}")
async def export_glossary(project_id: int, db: Session = Depends(get_db)):
    """Dump every glossary term for a project as plain JSON, mirrors corrections export."""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    terms = db.query(GlossaryTerm).filter(GlossaryTerm.project_id == project_id).all()
    return {
        "project_name": project.name,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "terms": [
            {"source_term": t.source_term, "target_term": t.target_term,
             "auto_detected": t.auto_detected, "occurrences": t.occurrences}
            for t in terms
        ],
    }


@router.post("/import/{project_id}")
async def import_glossary(project_id: int, payload: dict, db: Session = Depends(get_db)):
    """Re-import an exported glossary. Skips (source_term, target_term) pairs that already exist."""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    existing = {
        (t.source_term.lower(), t.target_term.lower())
        for t in db.query(GlossaryTerm).filter(GlossaryTerm.project_id == project_id).all()
    }

    added = 0
    for row in payload.get("terms", []):
        source_term = (row.get("source_term") or "").strip()
        target_term = (row.get("target_term") or "").strip()
        if not source_term or not target_term:
            continue
        key = (source_term.lower(), target_term.lower())
        if key in existing:
            continue

        db.add(GlossaryTerm(
            project_id=project_id, source_term=source_term, target_term=target_term,
            auto_detected=row.get("auto_detected", False), occurrences=row.get("occurrences", 1),
        ))
        existing.add(key)
        added += 1

    db.commit()
    return {"message": f"Imported {added} term(s).", "added": added}