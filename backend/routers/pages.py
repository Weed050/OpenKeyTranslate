
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

from core.database import get_db, SessionLocal
from core.config import get_setting
from models.models import Project, Page
from services.ocr_pipeline import process_image, to_original_coords
from services.translation_service import translate_bubbles
from services.page_export import save_ocr_json, page_paths, load_page_json, write_page_json
from services.inpaint_service import reinpaint_from_json
from services.glossary_service import load_glossary
from utils.bubble_post import carry_over
import threading
from utils.inpainting import erase_text_from_image
from services.job_queue import enqueue_page_processing, is_queued_or_running, OCR_LOCK
from utils.image_io import load_image_bgr, write_image, ImageLoadError

# Last failure reason per page (in memory). Shown by GET /pages/{id}/status so the editor can tell the
# user WHY a page is "failed" instead of a bare status string. Lost on restart (fine: re-run to see it again).
LAST_ERRORS: dict[int, str] = {}


def reset_stale_statuses(db: Session) -> int:
    """
    Call once at startup (main.py lifespan), when no worker can possibly be running:
    pages left as 'queued'/'processing' by a crash/kill would otherwise stay that way forever and the
    editor disables its "Run" button for them. They go back to 'pending'.
    """
    n = (db.query(Page).filter(Page.status.in_(("queued", "processing")))
         .update({Page.status: "pending"}, synchronize_session=False))
    db.commit()
    if n:
        print(f"[STARTUP] reset {n} stale queued/processing page(s) to pending.")
    return n

router = APIRouter(prefix="/pages", tags=["Pages"])


@router.get("/by-project/{project_id}")
async def list_pages(project_id: int, include_excluded: bool = False, db: Session = Depends(get_db)):
    """List every page across all chapters of a project, for the page picker / chapter nav. Excludes soft-deleted pages unless include_excluded=true."""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    return [
        {
            "page_id": page.id,
            "chapter_id": chapter.id,
            "chapter": chapter.number,
            "file_name": page.file_name,
            "order": page.order,
            "status": page.status,
            "error": LAST_ERRORS.get(page.id) if page.status == "failed" else None,
        }
        for chapter in project.chapters
        for page in chapter.pages
        if include_excluded or page.status != "excluded"
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
        "page_folder": paths["out_dir"],   # editor.js reads data.page_folder ("Open folder" button was a silent no-op)
        "has_lines": ocr_data.get("lines") is not None,                # False for pages processed before line data was saved
        "pending_translation": bool(ocr_data.get("pending_translation")),  # prefetched page: translate when opened (fresh memory)
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
def delete_bubble(page_id: int, bubble_id: str, db: Session = Depends(get_db)):
    """
    Remove a single bubble from this page's saved OCR/translation data - for OCR false positives (a "bubble"
    detected where there's no real text).

    The clean image is then REBUILT from the original file without that bubble's text lines
    (services/inpaint_service) - exact artwork comes back, nothing is pasted over neighbouring bubbles.
    Pages processed before line data existed fall back to pasting the original pixels back into the bubble box.
    Does not touch correction memory.
    """
    page = db.query(Page).filter(Page.id == page_id).first()
    if not page:
        raise HTTPException(status_code=404, detail="Page not found")

    paths = page_paths(page)
    if not os.path.exists(paths["json"]):
        raise HTTPException(status_code=404, detail="Page has not been OCR-processed yet")

    ocr_data = load_page_json(paths["json"])
    target_bubble = next((b for b in ocr_data.get("bubbles", []) if b["bubble_id"] == bubble_id), None)
    if target_bubble is None:
        raise HTTPException(status_code=404, detail="Bubble not found on this page")

    ocr_data["bubbles"] = [b for b in ocr_data.get("bubbles", []) if b["bubble_id"] != bubble_id]
    write_page_json(paths["json"], ocr_data)

    reinpainted = False
    try:
        reinpainted = reinpaint_from_json(paths, ocr_data)
    except Exception as e:
        print(f"[REINPAINT] failed after delete: {type(e).__name__}: {e}")
    if not reinpainted:
        _restore_bubble_artwork(paths, target_bubble)

    return {"message": "Bubble deleted", "remaining": len(ocr_data["bubbles"]), "reinpainted": reinpainted}


def _restore_bubble_artwork(paths: dict, bubble: dict) -> None:
    """
    Paste the original (pre-inpainting) pixels back into the bubble's box
    on the saved inpainted image. Best-effort: logs and continues on any
    failure instead of failing the whole delete.

    CAVEAT: only lines up 1:1 with raw/inpainted images when ocr_scale == 1.0
    (see this router's module docstring).
    """
    try:
        if not os.path.exists(paths["raw"]) or not os.path.exists(paths["image"]):
            return

        raw = cv2.imdecode(np.fromfile(paths["raw"], np.uint8), cv2.IMREAD_COLOR)
        inpainted = cv2.imdecode(np.fromfile(paths["image"], np.uint8), cv2.IMREAD_COLOR)
        if raw is None or inpainted is None or raw.shape != inpainted.shape:
            print(f"[RESTORE] raw/inpainted missing or shape mismatch - skipping artwork restore.")
            return

        xs = [p[0] for p in bubble["box"]]
        ys = [p[1] for p in bubble["box"]]
        margin = 6
        x1 = max(0, int(min(xs)) - margin)
        y1 = max(0, int(min(ys)) - margin)
        x2 = min(inpainted.shape[1], int(max(xs)) + margin)
        y2 = min(inpainted.shape[0], int(max(ys)) + margin)
        if x2 <= x1 or y2 <= y1:
            return

        inpainted[y1:y2, x1:x2] = raw[y1:y2, x1:x2]
        cv2.imwrite(paths["image"], inpainted)
        print(f"[RESTORE] Restored original artwork under deleted bubble at ({x1},{y1})-({x2},{y2}).")
    except Exception as e:
        print(f"[RESTORE] Failed to restore artwork for deleted bubble: {e}")


@router.post("/{page_id}/exclude")
async def exclude_page(page_id: int, db: Session = Depends(get_db)):
    """
    Soft-delete a page: marks it excluded so it drops out of the page
    list, chapter nav, and prev/next navigation - without touching files
    on disk. For scraped junk pages / stray images. Reversible via /restore.
    """
    page = db.query(Page).filter(Page.id == page_id).first()
    if not page:
        raise HTTPException(status_code=404, detail="Page not found")
    page.status = "excluded"
    db.commit()
    return {"message": "Page excluded", "page_id": page.id}


@router.post("/{page_id}/restore")
async def restore_page(page_id: int, db: Session = Depends(get_db)):
    """Undo /exclude - page reappears as 'processed' or 'pending' depending on prior state."""
    page = db.query(Page).filter(Page.id == page_id).first()
    if not page:
        raise HTTPException(status_code=404, detail="Page not found")
    if page.status != "excluded":
        raise HTTPException(status_code=400, detail="Page is not excluded")

    paths = page_paths(page)
    page.status = "processed" if os.path.exists(paths["json"]) else "pending"
    db.commit()
    return {"message": "Page restored", "page_id": page.id, "status": page.status}


def _bubble_edited(b: dict) -> bool:
    """User changed the AI draft (translation differs from the frozen ai_translation)."""
    return (b.get("translation") or "") != (b.get("ai_translation") or "")


@router.post("/{page_id}/retranslate")
def retranslate_page(page_id: int, overwrite_edited: bool = False, db: Session = Depends(get_db)):
    """
    Re-run translation only, reusing the OCR text/boxes already saved in this page's JSON - no OCR or inpainting.
    For pages where the AI failed / returned empty translations, after adding corrections or glossary terms,
    and for prefetched pages that wait for fresh memory (pending_translation).

    Bubbles the user already EDITED (translation != ai_translation) are KEPT unless overwrite_edited=true - the old
    version silently destroyed manual work. Skipped bubbles (numbers, "?!") are never translated.
    A retranslation becomes the new AI baseline ("Reset to AI" reverts to it).
    """
    page = db.query(Page).filter(Page.id == page_id).first()
    if not page:
        raise HTTPException(status_code=404, detail="Page not found")

    paths = page_paths(page)
    if not os.path.exists(paths["json"]):
        raise HTTPException(status_code=404, detail="Page has not been OCR-processed yet")

    ocr_data = load_page_json(paths["json"])
    all_bubbles = ocr_data.get("bubbles", [])
    keep = [b for b in all_bubbles if b.get("skip") or (not overwrite_edited and _bubble_edited(b))]
    keep_ids = {b["bubble_id"] for b in keep}
    work = [{"bubble_id": b["bubble_id"], "text": b["text"]} for b in all_bubbles if b["bubble_id"] not in keep_ids]
    if not all_bubbles:
        raise HTTPException(status_code=422, detail="No bubbles found on this page to translate")
    if not work:
        ocr_data.pop("pending_translation", None)
        write_page_json(paths["json"], ocr_data)
        return {"message": "Nothing to translate (all bubbles are edited or skipped)", "bubble_count": 0,
                "empty_count": 0, "kept_edited": len(keep_ids)}

    try:
        translated = translate_bubbles(work, project_id=page.chapter.project_id, page_id=page.id, db=db)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Translation failed: {e}")

    translation_by_id = {b["bubble_id"]: b["translation"] for b in translated}
    for b in ocr_data["bubbles"]:
        if b["bubble_id"] in translation_by_id:
            b["translation"] = b["ai_translation"] = translation_by_id[b["bubble_id"]]
    ocr_data.pop("pending_translation", None)
    write_page_json(paths["json"], ocr_data)

    empty_count = sum(1 for t in translation_by_id.values() if not t.strip())
    kept = len(keep_ids)
    return {
        "message": "Retranslated" + (f" ({empty_count} bubble(s) still came back empty)" if empty_count else "")
                   + (f"; kept {kept} edited/skipped bubble(s)" if kept else ""),
        "bubble_count": len(work),
        "empty_count": empty_count,
        "kept_edited": kept,
    }


@router.post("/{page_id}/bubbles/{bubble_id}/retranslate")
def retranslate_bubble(page_id: int, bubble_id: str, db: Session = Depends(get_db)):
    """
    Translate ONE bubble again (e.g. after fixing its source text). Up to 2 neighbours on each side are sent as
    context_only so the model still sees the scene; correction-memory hints and glossary terms apply as usual.
    Overwrites the bubble's translation + ai_translation (the editor confirms first when the user had edited it).
    """
    page = db.query(Page).filter(Page.id == page_id).first()
    if not page:
        raise HTTPException(status_code=404, detail="Page not found")
    paths = page_paths(page)
    if not os.path.exists(paths["json"]):
        raise HTTPException(status_code=404, detail="Page has not been OCR-processed yet")

    ocr_data = load_page_json(paths["json"])
    bubbles = ocr_data.get("bubbles", [])
    idx = next((i for i, b in enumerate(bubbles) if b["bubble_id"] == bubble_id), None)
    if idx is None:
        raise HTTPException(status_code=404, detail="Bubble not found on this page")
    target = bubbles[idx]
    if target.get("skip"):
        raise HTTPException(status_code=400, detail="This bubble is skipped - click 'Translate this' first")
    if not (target.get("text") or "").strip():
        raise HTTPException(status_code=422, detail="Bubble has no source text")

    context = bubbles[max(0, idx - 2):idx] + bubbles[idx + 1:idx + 3]
    work = [{"bubble_id": bubble_id, "text": target["text"]}]
    try:
        translated = translate_bubbles(work, project_id=page.chapter.project_id, page_id=page.id, db=db, context=context)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Translation failed: {e}")

    new_translation = translated[0].get("translation", "")
    target["translation"] = target["ai_translation"] = new_translation
    write_page_json(paths["json"], ocr_data)
    return {"message": "Bubble retranslated", "translation": new_translation, "ai_translation": new_translation}


@router.patch("/{page_id}/bubbles/{bubble_id}")
def patch_bubble(page_id: int, bubble_id: str, payload: dict, db: Session = Depends(get_db)):
    """
    Edit a bubble's SOURCE side (the translation text itself is saved via /corrections):
      {"text": "..."}  fix an OCR mistake (the previous text is kept once in text_raw; nothing is retranslated here -
                       the editor offers 'Retranslate bubble' next)
      {"skip": true}   do not translate this bubble and put the original pixels back (skip_reason = "manual")
      {"skip": false}  translate it after all (page numbers etc. can be forced); the text is erased again
    A skip change rebuilds the clean image from the saved line data when the page has it.
    """
    page = db.query(Page).filter(Page.id == page_id).first()
    if not page:
        raise HTTPException(status_code=404, detail="Page not found")
    paths = page_paths(page)
    if not os.path.exists(paths["json"]):
        raise HTTPException(status_code=404, detail="Page has not been OCR-processed yet")

    ocr_data = load_page_json(paths["json"])
    bubble = next((b for b in ocr_data.get("bubbles", []) if b["bubble_id"] == bubble_id), None)
    if bubble is None:
        raise HTTPException(status_code=404, detail="Bubble not found on this page")

    skip_changed = False
    if "text" in payload:
        new_text = str(payload["text"]).strip()
        if not new_text:
            raise HTTPException(status_code=400, detail="Source text cannot be empty (use Delete or Skip instead)")
        if new_text != bubble["text"]:
            bubble.setdefault("text_raw", bubble["text"])
            bubble["text"] = new_text
            if bubble.get("skip") and bubble.get("skip_reason") != "manual":
                bubble.pop("skip", None)          # text now contains letters -> no longer a number/symbol bubble
                bubble.pop("skip_reason", None)
                skip_changed = True
    if "skip" in payload:
        want = bool(payload["skip"])
        if want != bool(bubble.get("skip")):
            skip_changed = True
            if want:
                bubble["skip"], bubble["skip_reason"] = True, "manual"
            else:
                bubble.pop("skip", None)
                bubble.pop("skip_reason", None)

    write_page_json(paths["json"], ocr_data)

    reinpainted = needs_reocr = False
    if skip_changed:
        try:
            reinpainted = reinpaint_from_json(paths, ocr_data)
        except Exception as e:
            print(f"[REINPAINT] failed after patch: {type(e).__name__}: {e}")
        needs_reocr = not reinpainted
    return {"bubble": bubble, "reinpainted": reinpainted, "needs_reocr": needs_reocr}


@router.post("/{page_id}/reinpaint")
def reinpaint_page(page_id: int, db: Session = Depends(get_db)):
    """Rebuild the clean image from the original + saved text lines (after deletes / skips). No OCR, no translation."""
    page = db.query(Page).filter(Page.id == page_id).first()
    if not page:
        raise HTTPException(status_code=404, detail="Page not found")
    paths = page_paths(page)
    if not os.path.exists(paths["json"]):
        raise HTTPException(status_code=404, detail="Page has not been OCR-processed yet")
    try:
        ok = reinpaint_from_json(paths, load_page_json(paths["json"]))
    except ImageLoadError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Raw image not found")
    if not ok:
        raise HTTPException(status_code=409, detail="This page was processed before line data was saved - use Re-OCR once.")
    return {"message": "Clean image rebuilt"}


_SYNC_RUNNING: set[int] = set()
_SYNC_LOCK = threading.Lock()


def _claim(page_id: int) -> bool:
    """One pipeline run per page at a time (sync button, re-OCR and the background worker all share this)."""
    with _SYNC_LOCK:
        if page_id in _SYNC_RUNNING or is_queued_or_running(page_id):
            return False
        _SYNC_RUNNING.add(page_id)
        return True


def _release(page_id: int) -> None:
    with _SYNC_LOCK:
        _SYNC_RUNNING.discard(page_id)


def _process_page_pipeline(page_id: int, reocr: bool = False, translate: bool = True) -> dict:
    """
    Runs the full OCR -> translate -> inpaint pipeline for one page and persists results. Opens its OWN DB session -
    required to be safe from a background thread. Sets page.status along the way ("processing" -> "processed",
    or "failed") so the frontend can poll and reflect real state.

    reocr=True   page already processed: OCR + inpainting run again, but translations / manual skips / corrected
                 source text of bubbles whose text did not change are CARRIED OVER (utils.bubble_post.carry_over),
                 only new or changed bubbles are translated. A failure leaves the old result and status intact.
    translate=False  OCR + inpainting only; the JSON is flagged pending_translation and the editor translates when the
                 page is OPENED - with all corrections made since (prefetch used to translate immediately, i.e.
                 BEFORE the user had corrected the previous page, which defeats the correction-memory idea).
    """
    db = SessionLocal()
    previous_status = None
    try:
        page = db.query(Page).filter(Page.id == page_id).first()
        if not page:
            raise ValueError(f"Page {page_id} not found")

        previous_status = page.status
        page.status = "processing"
        db.commit()

        paths = page_paths(page)
        if not os.path.exists(paths["raw"]):
            raise ValueError(f"Raw image not found at {paths['raw']}")

        image = load_image_bgr(paths["raw"])   # raises ImageLoadError with the real reason (format sniffing + Pillow fallback)
        project_id = page.chapter.project_id
        protected = [t.source_term for t in load_glossary(project_id, db)]   # names the text cleanup must not touch

        with OCR_LOCK:   # never two PaddleOCR runs at once (sync /process vs background worker)
            ocr_result = process_image(image, protected)

        old_bubbles = []
        if reocr and os.path.exists(paths["json"]):
            old_bubbles = load_page_json(paths["json"]).get("bubbles", [])
        carried = carry_over(old_bubbles, ocr_result["bubbles"]) if old_bubbles else set()

        # A translation failure (no internet / DNS, exhausted keys, API outage) must NOT throw away minutes of GPU
        # OCR work: keep the bubbles with empty translations, mark the page processed and report the error - the
        # editor shows "(no AI translation)" and Retranslate re-runs ONLY the translation later.
        translation_error = None
        pending = False
        to_translate = [b for b in ocr_result["bubbles"] if b["bubble_id"] not in carried]
        if get_setting("translation_on", True) and translate:
            try:
                translate_bubbles(to_translate, project_id=project_id, page_id=page.id, db=db)
            except Exception as e:
                translation_error = f"{type(e).__name__}: {e}"
                print(f"[PIPELINE] page {page_id}: translation FAILED, OCR result kept -> {translation_error}")
                db.rollback()
                for b in to_translate:
                    b.setdefault("translation", "")
        elif translate is False and get_setting("translation_on", True):
            pending = True
            for b in to_translate:
                b.setdefault("translation", "")

        os.makedirs(paths["out_dir"], exist_ok=True)
        save_ocr_json(paths["out_dir"], paths["file_base_name"], ocr_result, pending_translation=pending)

        # numbers / "?!" bubbles keep their original pixels: only inpaint_lines are erased
        inpainted_image = erase_text_from_image(image, ocr_result.get("inpaint_lines", ocr_result["lines"]), to_original_coords)
        write_image(paths["image"], inpainted_image)

        page.status = "processed"
        db.commit()
        LAST_ERRORS.pop(page_id, None)

        skipped = sum(1 for b in ocr_result["bubbles"] if b.get("skip"))
        return {"page_id": page_id, "bubble_count": len(ocr_result["bubbles"]), "skipped": skipped,
                "carried_over": len(carried), "translation_error": translation_error, "pending_translation": pending}
    except Exception as e:
        LAST_ERRORS[page_id] = f"{type(e).__name__}: {e}"
        db.rollback()
        page = db.query(Page).filter(Page.id == page_id).first()
        if page:
            # failed Re-OCR of a page that was fine must not turn it into a "failed" page
            page.status = "processed" if (reocr and previous_status == "processed") else "failed"
            db.commit()
        raise
    finally:
        db.close()


def _run_sync(page_id: int, **kwargs) -> dict:
    if not _claim(page_id):
        raise HTTPException(status_code=409, detail="This page is already being processed - wait for it to finish.")
    try:
        return _process_page_pipeline(page_id, **kwargs)
    except ImageLoadError as e:                      # file exists but is not a decodable image: NOT a 404
        raise HTTPException(status_code=422, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Pipeline failed: {e}")
    finally:
        _release(page_id)


@router.post("/{page_id}/process")
def process_page(page_id: int, db: Session = Depends(get_db)):
    """
    Run OCR -> translation -> inpainting for one page, synchronously - blocks until done. Used by the editor's manual
    "Run OCR + translation" button. See /process-async for the non-blocking background variant.
    """
    page = db.query(Page).filter(Page.id == page_id).first()
    if not page:
        raise HTTPException(status_code=404, detail="Page not found")

    result = _run_sync(page_id)
    message = "Page processed"
    if result.get("translation_error"):
        message += " - BUT translation failed (OCR kept). Fix the connection/keys, then click Retranslate."
    return {"message": message, **result}


@router.post("/{page_id}/reocr")
def reocr_page(page_id: int, db: Session = Depends(get_db)):
    """
    Run OCR + inpainting AGAIN on an already processed page (changed ignore patterns / text-cleanup options, bad OCR,
    old page without line data). Translations and manual edits of bubbles whose text is unchanged are carried over;
    new / changed bubbles are translated. Blocks until done.
    """
    page = db.query(Page).filter(Page.id == page_id).first()
    if not page:
        raise HTTPException(status_code=404, detail="Page not found")
    result = _run_sync(page_id, reocr=True)
    msg = f"Re-OCR done: {result['bubble_count']} bubble(s), {result['carried_over']} kept their translation"
    if result.get("translation_error"):
        msg += " - BUT translating the new ones failed (use Retranslate)."
    return {"message": msg, **result}


@router.post("/{page_id}/process-async")
def process_page_async(page_id: int, translate: bool = True, db: Session = Depends(get_db)):
    """
    Non-blocking variant of /process: schedules the pipeline on the background worker (services/job_queue.py) and
    returns immediately. Used to prefetch the next page: with translate=false only OCR + inpainting run and the
    translation happens when the page is opened (see _process_page_pipeline).
    """
    page = db.query(Page).filter(Page.id == page_id).first()
    if not page:
        raise HTTPException(status_code=404, detail="Page not found")

    if page.status == "processed":
        return {"message": "Already processed", "page_id": page_id, "status": "processed"}

    if not os.path.exists(page_paths(page)["raw"]):
        raise HTTPException(status_code=404, detail="Raw image not found")

    scheduled = enqueue_page_processing(page_id, lambda: _process_page_pipeline(page_id, translate=translate))

    if scheduled:
        page.status = "queued"
        db.commit()
        return {"message": "Queued for background processing", "page_id": page_id, "status": "queued"}
    return {"message": "Already queued or running", "page_id": page_id, "status": page.status}


@router.get("/{page_id}/status")
async def get_page_status(page_id: int, db: Session = Depends(get_db)):
    """Lightweight polling endpoint - just the page's current status string."""
    page = db.query(Page).filter(Page.id == page_id).first()
    if not page:
        raise HTTPException(status_code=404, detail="Page not found")
    return {
        "page_id": page_id,
        "status": page.status,
        "error": LAST_ERRORS.get(page_id) if page.status == "failed" else None,
        "raw_path": page_paths(page)["raw"],   # editor: "Show file" button on the failed-page screen
    }
