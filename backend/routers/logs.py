
# backend/routers/logs.py

"""
API Router exposing TranslationLog rows for the "Debug / TM Analytics"
panel: the paired zero-shot vs. memory-injected translations the thesis
experiment is evaluated from (see services/translation_service.py).

Read-only by design - logs are historical experiment data and are never
fed back into translation (see the module docstring in
services/translation_service.py). Nothing here ever writes.

The list also says WHERE each row belongs (chapter, page file, the bubble's number on the page now) and what the
bubble holds now (current translation, reviewed or not) - read from the page JSON, never stored in the log, so the
logged experiment fields keep their meaning.
"""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func

from core.database import get_db
from models.models import Project, Page, Chapter, TranslationLog
from services.page_export import page_paths, load_page_json, bubble_reviewed


def _iso(dt):
    """SQLite drops the time zone: the stored values are UTC, so say so (the browser shows local time)."""
    if dt is None:
        return None
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).isoformat()


def _page_info(page) -> dict:
    """Chapter / file of a page and, per bubble id, its number on the page now + current translation + reviewed."""
    info = {"chapter": page.chapter.number if page.chapter else None, "file_name": page.file_name, "bubbles": {}}
    try:
        data = load_page_json(page_paths(page)["json"])
    except Exception:
        return info
    for i, b in enumerate(data.get("bubbles", [])):
        info["bubbles"][b.get("bubble_id")] = {"no": i + 1, "translation": b.get("translation") or "",
                                               "reviewed": bubble_reviewed(b), "skip": bool(b.get("skip"))}
    return info

router = APIRouter(prefix="/logs", tags=["Logs"])


@router.get("/by-project/{project_id}")
async def list_translation_logs(project_id: int, db: Session = Depends(get_db)):
    """
    List every TranslationLog row for a project, newest first.

    Grouped client-side by run_id + bubble_id to pair up the zero_shot and
    memory_injected variants written from the same translation pass (see
    services/translation_service.translate_bubbles).
    """
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    logs = (
        db.query(TranslationLog)
        .join(Page, TranslationLog.page_id == Page.id)
        .join(Chapter, Page.chapter_id == Chapter.id)
        .filter(Chapter.project_id == project_id)
        .order_by(TranslationLog.created_at.desc())
        .all()
    )

    pages: dict[int, dict] = {}

    def where(log) -> dict:
        if log.page_id not in pages:
            page = db.query(Page).filter(Page.id == log.page_id).first()
            pages[log.page_id] = _page_info(page) if page else {"chapter": None, "file_name": None, "bubbles": {}}
        info = pages[log.page_id]
        bubble = info["bubbles"].get(log.bubble_id)
        return {"chapter": info["chapter"], "file_name": info["file_name"],
                "bubble_no": bubble["no"] if bubble else None,          # None: the bubble was deleted / merged since
                "current_translation": bubble["translation"] if bubble else None,
                "reviewed": bubble["reviewed"] if bubble else None}

    return [
        {
            "id": log.id,
            "run_id": log.run_id,
            "page_id": log.page_id,
            **where(log),
            "bubble_id": log.bubble_id,
            "variant": log.variant,
            "source_text": log.source_text,
            "output_text": log.output_text,
            "matched_correction_id": log.matched_correction_id,
            "similarity_score": log.similarity_score,
            "threshold_used": log.threshold_used,
            "glossary_terms_used": log.glossary_terms_used,   # was missing: glossary effect could not be analysed
            "hints_json": log.hints_json,
            "memory_lane": log.memory_lane,
            "injection_json": log.injection_json,
            "model_used": log.model_used,
            "key_label": log.key_label,
            "created_at": _iso(log.created_at),
        }
        for log in logs
    ]

@router.get("/stats/by-project/{project_id}")
async def translation_stats(project_id: int, db: Session = Depends(get_db)):
    """Per-key request counts (total + last 24h) - spot which key in the pool is getting hammered."""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    base_query = (
        db.query(TranslationLog.key_label, TranslationLog.model_used, func.count(TranslationLog.id))
        .join(Page, TranslationLog.page_id == Page.id)
        .join(Chapter, Page.chapter_id == Chapter.id)
        .filter(Chapter.project_id == project_id)
    )
    total_counts = base_query.group_by(TranslationLog.key_label, TranslationLog.model_used).all()
    recent_counts = base_query.filter(TranslationLog.created_at >= cutoff) \
        .group_by(TranslationLog.key_label, TranslationLog.model_used).all()
    recent_map = {(k, m): c for k, m, c in recent_counts}

    return [
        {"key_label": k or "unknown", "model_used": m or "unknown", "total_requests": c, "last_24h": recent_map.get((k, m), 0)}
        for k, m, c in total_counts
    ]

@router.get("/export/{project_id}")
async def export_logs(project_id: int, db: Session = Depends(get_db)):
    """Dump every TranslationLog row for a project as JSON, for offline analysis."""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    logs = (
        db.query(TranslationLog)
        .join(Page, TranslationLog.page_id == Page.id)
        .join(Chapter, Page.chapter_id == Chapter.id)
        .filter(Chapter.project_id == project_id)
        .order_by(TranslationLog.created_at.asc())
        .all()
    )
    return {
        "project_name": project.name,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "logs": [
            {
                "run_id": log.run_id, "page_id": log.page_id, "bubble_id": log.bubble_id,
                "variant": log.variant, "source_text": log.source_text, "output_text": log.output_text,
                "matched_correction_id": log.matched_correction_id, "similarity_score": log.similarity_score,
                "threshold_used": log.threshold_used, "model_used": log.model_used,
                "glossary_terms_used": log.glossary_terms_used, "hints_json": log.hints_json,
                "memory_lane": log.memory_lane, "injection_json": log.injection_json,
                "key_label": log.key_label, "created_at": log.created_at.isoformat() if log.created_at else None,
            }
            for log in logs
        ],
    }
