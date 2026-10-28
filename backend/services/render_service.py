
# backend/services/render_service.py

"""
Putting the translation back on the page: preview in the editor and export of finished pages.

  clean page (text erased)  +  every bubble's translation  ->  PNG
The text is fitted into the bubble interior found by the shared flood fill (utils/typesetting.py -> utils/bubble_interior.py).
Bubbles that are skipped (page numbers, "?!") or have no translation are NOT drawn - the typesetter would otherwise
draw the English source text for an empty translation.
"""

import os

from services.page_export import load_page_json, page_paths
from utils.image_io import load_image_bgr, write_image
from utils.typesetting import draw_translated_text_on_clean_image


def render_page(paths: dict, ocr_data: dict | None = None):
    """Return the page image with the translated text drawn in (BGR array). Raises FileNotFoundError without a clean image."""
    if ocr_data is None:
        ocr_data = load_page_json(paths["json"])
    if not os.path.exists(paths["image"]):
        raise FileNotFoundError(paths["image"])
    clean = load_image_bgr(paths["image"])
    original = load_image_bgr(paths["raw"]) if os.path.exists(paths["raw"]) else clean
    bubbles = [
        {"bubble_id": b["bubble_id"], "text": b["text"], "translation": b["translation"], "box_coords": b["box"],
         "line_count": b.get("line_count", 1)}
        for b in ocr_data.get("bubbles", [])
        if not b.get("skip") and (b.get("translation") or "").strip()
    ]
    if not bubbles:
        return clean
    return draw_translated_text_on_clean_image(clean, original, bubbles, lambda box: box)


def export_chapters(job: dict, project_id: int, chapter_ids: list[int] | None, dest_root: str,
                    include_untranslated: bool = False) -> dict:
    """
    Background job: write <dest_root>/<project>/<chapter>/page_NNN.png for every processed page.
    Pages without a single translation are skipped unless include_untranslated (then the clean page is exported).
    """
    from core.database import SessionLocal
    from models.models import Chapter, Page, Project
    from services.backup_service import BackupError

    if not os.path.isdir(dest_root):
        raise BackupError(f"Destination folder does not exist: {dest_root}")
    db = SessionLocal()
    try:
        project = db.query(Project).filter(Project.id == project_id).first()
        if project is None:
            raise BackupError("Project not found.")
        chapters = sorted(project.chapters, key=lambda c: c.number)
        if chapter_ids:
            chapters = [c for c in chapters if c.id in set(chapter_ids)]
        work = [(c, p) for c in chapters for p in sorted(c.pages, key=lambda p: p.order or 0) if p.status == "processed"]
        job["total_bytes"] = len(work)                 # the UI progress bar counts pages here
        job["step"] = "rendering pages"
        written = skipped = 0
        for chapter, page in work:
            paths = page_paths(page)
            try:
                data = load_page_json(paths["json"])
                translated = any((b.get("translation") or "").strip() and not b.get("skip") for b in data.get("bubbles", []))
                if translated or include_untranslated:
                    out_dir = os.path.join(dest_root, project.name, chapter.number)
                    os.makedirs(out_dir, exist_ok=True)
                    write_image(os.path.join(out_dir, os.path.splitext(page.file_name)[0] + ".png"), render_page(paths, data))
                    written += 1
                else:
                    skipped += 1
            except FileNotFoundError:
                skipped += 1
            job["done_bytes"] += 1
        return {"message": f"Exported {written} page(s) to {os.path.join(dest_root, project.name)}"
                           + (f" ({skipped} without translation skipped)" if skipped else ""),
                "written": written, "skipped": skipped, "path": os.path.join(dest_root, project.name)}
    finally:
        db.close()
