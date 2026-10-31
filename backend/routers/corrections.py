
# backend/routers/corrections.py

"""
API Router for the correction-memory system: confirming/persisting a user's
translation for a bubble, browsing the stored corrections for a project (the
"TM analytics" / memory browser panel), editing (inline / find & replace) and deleting stale entries, and
reporting which entries the memory contributed to one bubble (the editor's "Used from memory" panel).
"""

import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from core.database import get_db
from models.models import Page, Project, Correction, TranslationLog
from models.schemas import CorrectionSchema, CorrectionEditSchema, MemoryReplaceSchema
from core.config import get_setting
from services.memory_service import (save_correction, delete_correction, encode_embedding, embed_text,
                                       get_correction_usage, memory_text, reembed_project, edit_correction,
                                       replace_in_memory, normalize_phrase)
from services.glossary_service import suggestions_for_correction
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

    Three things happen, independently:
    1. The page's own saved JSON is updated so `translation` reflects what
       the user just typed (see services/page_export.update_bubble_translation) -
       this always happens, so reopening the editor shows the edit.
    2. save_correction() decides separately whether this is *also* worth
       adding to the translation memory: an edit becomes kind="corrected", an unchanged translation kind="approved"
       only when explicitly approved (payload.approve) with memory_store_approved on; a too-short fragment or a
       duplicate is skipped, returning correction_id: null. Step 1 still stamps the bubble as reviewed.
    3. For a "corrected" entry: glossary suggestions this correction takes part in (a term fixed in >= 2
       corrections) are returned so the editor can offer them right away (glossary_suggest_on_save).
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
        approve=payload.approve,
    )

    if correction is None:
        return {"message": "Saved (not added to memory: duplicate, too short, or approvals are off)",
                "correction_id": None, "kind": None, "glossary_suggestions": []}

    suggestions = []
    if correction.kind == "corrected" and get_setting("glossary_suggest_on_save", True):
        try:
            suggestions = suggestions_for_correction(correction, db)
        except Exception as e:   # a suggestion problem must never turn a successful save into an error
            print(f"[GLOSSARY] on-save suggestions failed: {type(e).__name__}: {e}")
    return {"message": "Correction saved" if correction.kind == "corrected" else "Approved translation saved to memory",
            "correction_id": correction.id, "kind": correction.kind, "glossary_suggestions": suggestions}


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
            "kind": c.kind or "corrected",
            "created_at": c.created_at.isoformat() if c.created_at else None,
            "edited_at": c.edited_at.isoformat() if c.edited_at else None,
            "final_before_edit": c.final_before_edit,
        }
        for c in corrections
    ]


@router.patch("/{correction_id}")
async def update_correction(correction_id: int, payload: CorrectionEditSchema, db: Session = Depends(get_db)):
    """Inline edit of an entry's final translation (Memory page). The first-saved text is kept in final_before_edit."""
    if not payload.final_translation.strip():
        raise HTTPException(status_code=422, detail="The translation must not be empty - delete the entry instead")
    correction = edit_correction(db, correction_id, payload.final_translation)
    if correction is None:
        raise HTTPException(status_code=404, detail="Correction not found")
    return {"message": "Memory entry updated", "id": correction.id, "final_translation": correction.final_translation,
            "kind": correction.kind, "edited_at": correction.edited_at.isoformat() if correction.edited_at else None}


@router.post("/replace/{project_id}")
async def replace_corrections(project_id: int, payload: MemoryReplaceSchema, db: Session = Depends(get_db)):
    """Find & replace in the project's memory: apply=false = preview, apply=true = change (optionally only `ids`)."""
    if not db.query(Project).filter(Project.id == project_id).first():
        raise HTTPException(status_code=404, detail="Project not found")
    if len(payload.find.strip()) < 2:
        raise HTTPException(status_code=422, detail="Search text must have at least 2 characters")
    mode = "prefix" if payload.mode == "prefix" else "word"
    changes = replace_in_memory(db, project_id, payload.find, payload.replace, mode, payload.ids, payload.apply)
    return {"applied": payload.apply, "count": len(changes), "changes": changes}


@router.get("/used/{page_id}/{bubble_id}")
async def memory_used_for_bubble(page_id: int, bubble_id: str, db: Session = Depends(get_db)):
    """
    What the correction memory contributed to this bubble's CURRENT AI translation (its latest translation run):
    the injected examples (fuzzy lane) or the verbatim "exact" entry, each with its memory entry id so the editor can
    open it on the Memory page, plus the glossary terms. Logs written before correction_ids were recorded are
    matched back to entries by their source text.
    """
    page = db.query(Page).filter(Page.id == page_id).first()
    if not page:
        raise HTTPException(status_code=404, detail="Page not found")
    rows = (db.query(TranslationLog)
            .filter(TranslationLog.page_id == page_id, TranslationLog.bubble_id == bubble_id)
            .order_by(TranslationLog.id.desc()).all())
    empty = {"lane": None, "entries": [], "glossary": [], "translated_at": None}
    if not rows:
        return empty
    latest_run = rows[0].run_id
    injected = next((r for r in rows if r.run_id == latest_run and r.variant == "memory_injected"), None)
    if injected is None:
        glossary = json.loads(rows[0].glossary_terms_used) if rows[0].glossary_terms_used else []
        return {**empty, "glossary": glossary, "translated_at": rows[0].created_at.isoformat() if rows[0].created_at else None}

    try:
        injection = json.loads(injected.injection_json or "{}")
    except json.JSONDecodeError:
        injection = {}
    if not injection.get("examples") and not injection.get("exact") and injected.hints_json:
        injection = {**json.loads(injected.hints_json), **injection}
    project_corrections = db.query(Correction).filter(Correction.project_id == page.chapter.project_id).all()
    by_id = {c.id: c for c in project_corrections}

    def latest_for_source(en: str):
        key = normalize_phrase(memory_text(en or ""))
        hits = [c for c in project_corrections if normalize_phrase(memory_text(c.source_text)) == key]
        return max(hits, key=lambda c: c.id) if hits else None

    def describe(correction, shown: dict) -> dict:
        return {
            "correction_id": correction.id if correction else None,
            "exists": correction is not None,
            "en": shown.get("en") or (correction.source_text if correction else ""),
            "pl_shown": shown.get("pl", ""),                       # what the model was given at the time
            "pl_now": correction.final_translation if correction else None,   # memory today (after edits)
            "similarity": shown.get("similarity"),
            "kind": shown.get("kind") or (correction.kind if correction else None),
        }

    ids = injection.get("correction_ids") or []
    entries = []
    if injection.get("exact"):
        c = by_id.get(ids[0] if ids else injected.matched_correction_id)
        entries.append(describe(c, {"en": injected.source_text, "pl": injection["exact"], "similarity": 1.0}))
    for i, ex in enumerate(injection.get("examples") or []):
        c = by_id.get(ids[i]) if i < len(ids) else latest_for_source(ex.get("en"))
        entries.append(describe(c, ex))
    return {"lane": injected.memory_lane or injection.get("lane"), "entries": entries,
            "glossary": injection.get("glossary", []),
            "translated_at": injected.created_at.isoformat() if injected.created_at else None}


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

    return get_correction_usage(correction_id, db)


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
                "kind": c.kind or "corrected",
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
            kind=row.get("kind") or "corrected",
            embedding=encode_embedding(embed_text(memory_text(source_text))),
        )
        db.add(correction)
        existing.add(key)
        added += 1

    db.commit()
    return {"message": f"Imported {added} correction(s).", "added": added}


@router.post("/reembed/{project_id}")
def reembed_corrections(project_id: int, db: Session = Depends(get_db)):
    """
    Recompute the embeddings of every memory entry from the normalized source text (OCR cleanup applied).
    Run once for entries saved before the cleanup existed ("SQUAD- RON") or after changing the embedding model.
    """
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    count = reembed_project(db, project_id)
    return {"message": f"Re-embedded {count} memory entr{'y' if count == 1 else 'ies'}.", "count": count}
