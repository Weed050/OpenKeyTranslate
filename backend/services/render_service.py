
# backend/services/render_service.py

"""
Putting the translation back on the page: preview in the editor, the editor's Lettering mode and export of pages.

  clean page (text erased)  +  every bubble's translation  ->  image
The text is fitted into the bubble interior found by the shared flood fill (utils/typesetting.py -> utils/bubble_interior.py)
unless the user placed it in Lettering mode (bubble "layout": area, font size, alignment, angle, outline).
Bubbles that are skipped (page numbers, "?!") or have no translation are NOT drawn - the typesetter would otherwise
draw the English source text for an empty translation.

Export (one page from the editor, many from the project page) writes into <project folder>/exported/<chapter>/ by
default - next to raw/ and processed/ - or into a folder the user picks, as loose files or one ZIP:
  content  "translated" (with text) | "clean" (text erased) | "both" (clean copies get a "_clean" suffix)
  fmt      "png" | "jpg" (quality 50-100)
"""

import os
import zipfile
from datetime import datetime

import cv2

from services.page_export import load_page_json, page_paths
from utils.image_io import load_image_bgr
from utils.typesetting import draw_translated_text_on_clean_image, plan_page

EXPORT_CONTENT = ("translated", "clean", "both")
EXPORT_FORMATS = ("png", "jpg")


def _drawable(ocr_data: dict) -> list[dict]:
    """Bubbles to draw; text_rect = slant of tilted original text (map labels), from the words' boxes in the JSON."""
    from services.bubble_edit import bubble_text_rect
    return [
        {"bubble_id": b["bubble_id"], "text": b["text"], "translation": b["translation"], "box_coords": b["box"],
         "line_count": b.get("line_count", 1), "layout": b.get("layout"), "text_rect": bubble_text_rect(ocr_data, b)}
        for b in ocr_data.get("bubbles", [])
        if not b.get("skip") and (b.get("translation") or "").strip()
    ]


def _images(paths: dict):
    if not os.path.exists(paths["image"]):
        raise FileNotFoundError(paths["image"])
    clean = load_image_bgr(paths["image"])
    original = load_image_bgr(paths["raw"]) if os.path.exists(paths["raw"]) else clean
    return clean, original


def render_page(paths: dict, ocr_data: dict | None = None):
    """Return the page image with the translated text drawn in (BGR array). Raises FileNotFoundError without a clean image."""
    if ocr_data is None:
        ocr_data = load_page_json(paths["json"])
    clean, original = _images(paths)
    bubbles = _drawable(ocr_data)
    if not bubbles:
        return clean
    return draw_translated_text_on_clean_image(clean, original, bubbles, lambda box: box)


def page_layout(paths: dict, ocr_data: dict | None = None) -> list[dict]:
    """
    Where each drawn bubble's text sits right now (Lettering mode draws these areas): [{bubble_id, box, manual_box,
    manual_size, font_size, angle, align, stroke, overflow, lines}] in image coordinates.
    """
    if ocr_data is None:
        ocr_data = load_page_json(paths["json"])
    clean, original = _images(paths)
    return [{"bubble_id": p["bubble_id"], "box": list(p["box"]), "manual_box": p["manual_box"], "manual_size": p["manual_size"],
             "font_size": p["size"], "angle": p["angle"], "manual_angle": p["manual_angle"], "align": p["align"], "stroke": p["stroke"],
             "valign": p["valign"], "font": p["font_name"], "bold": p["bold"], "italic": p["italic"], "color": p["color"],
             "overflow": p["overflow"], "lines": len(p["lines"])}
            for p in plan_page(clean, original, _drawable(ocr_data))]


def encode_image(image, fmt: str = "png", quality: int = 92) -> bytes:
    if fmt == "jpg":
        ok, buf = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, max(50, min(100, int(quality)))])
    else:
        ok, buf = cv2.imencode(".png", image)
    if not ok:
        raise ValueError("Could not encode the page image")
    return buf.tobytes()


def page_exports(paths: dict, file_stem: str, content: str = "translated", fmt: str = "png", quality: int = 92,
                 data: dict | None = None) -> list[tuple[str, bytes]]:
    """[(file name, bytes)] of one page for an export: the translated and / or the clean image."""
    data = data if data is not None else load_page_json(paths["json"])
    ext = "jpg" if fmt == "jpg" else "png"
    out = []
    if content in ("translated", "both"):
        out.append((f"{file_stem}.{ext}", encode_image(render_page(paths, data), fmt, quality)))
    if content in ("clean", "both"):
        clean, _ = _images(paths)
        out.append((f"{file_stem}{'_clean' if content == 'both' else ''}.{ext}", encode_image(clean, fmt, quality)))
    return out


def default_export_root(project) -> str:
    """<project folder>/exported - next to raw/ and processed/."""
    return os.path.join(project.workspace_path, "exported")


def export_one_page(page, content: str = "translated", fmt: str = "png", quality: int = 92) -> list[str]:
    """The editor's "Export page": into <project>/exported/<chapter>/. Returns the written file paths."""
    paths = page_paths(page)
    out_dir = os.path.join(default_export_root(page.chapter.project), page.chapter.number)
    os.makedirs(out_dir, exist_ok=True)
    written = []
    for name, blob in page_exports(paths, os.path.splitext(page.file_name)[0], content, fmt, quality):
        target = os.path.join(out_dir, name)
        with open(target, "wb") as f:
            f.write(blob)
        written.append(target)
    if content != "clean":       # a clean-only export is material for lettering elsewhere, not a finished page
        from services.page_progress import record_exports
        record_exports(page.chapter.project, [page.id], out_dir)
    return written


def export_chapters(job: dict, project_id: int, chapter_ids: list[int] | None, dest_root: str | None,
                    include_untranslated: bool = False, content: str = "translated", fmt: str = "png",
                    quality: int = 92, as_zip: bool = False, only_reviewed: bool = False) -> dict:
    """
    Background job: every processed page of the chosen chapters, as files in <dest>/<chapter>/ or one ZIP in <dest>.
    dest_root None / "" = <project folder>/exported; a folder the user picked gets a <project> sub-folder.
    Pages without a single translation are skipped unless include_untranslated (then the clean page is exported),
    except for content "clean", which exports every processed page.
    only_reviewed: pages not fully reviewed by the user (AI drafts, partly translated) are skipped too - the default of
    the export dialog; exporting AI drafts is a conscious choice there (with a warning).
    Every page written with its translation is stamped in <project>/export_log.json (services/page_progress.py), so the
    "exported" stage also knows exports to another folder or a ZIP.
    """
    from services.page_progress import record_exports
    from services.page_export import page_stage
    from core.database import SessionLocal
    from models.models import Project
    from services.backup_service import BackupError

    content = content if content in EXPORT_CONTENT else "translated"
    fmt = fmt if fmt in EXPORT_FORMATS else "png"
    db = SessionLocal()
    try:
        project = db.query(Project).filter(Project.id == project_id).first()
        if project is None:
            raise BackupError("Project not found.")
        if dest_root:
            if not os.path.isdir(dest_root):
                raise BackupError(f"Destination folder does not exist: {dest_root}")
            target_root = os.path.join(dest_root, project.name)
        else:
            target_root = default_export_root(project)
        chapters = sorted(project.chapters, key=lambda c: c.number)
        if chapter_ids:
            chapters = [c for c in chapters if c.id in set(chapter_ids)]
        work = [(c, p) for c in chapters for p in sorted(c.pages, key=lambda p: p.order or 0) if p.status == "processed"]
        job["total_bytes"] = len(work)                 # the UI progress bar counts pages here
        job["step"] = "rendering pages"

        zip_path = None
        archive = None
        if as_zip:
            os.makedirs(target_root, exist_ok=True)
            zip_path = os.path.join(target_root, f"{project.name}_export_{datetime.now():%Y%m%d-%H%M%S}.zip")
            archive = zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_STORED)   # PNG / JPG are compressed already
        written = skipped = unreviewed = 0
        stamped = []
        try:
            for chapter, page in work:
                paths = page_paths(page)
                try:
                    data = load_page_json(paths["json"])
                    translated = any((b.get("translation") or "").strip() and not b.get("skip") for b in data.get("bubbles", []))
                    if not (translated or include_untranslated or content == "clean"):
                        skipped += 1
                        continue
                    if only_reviewed and content != "clean" and page_stage(paths["json"])["stage"] not in ("reviewed", "checked"):
                        unreviewed += 1
                        continue
                    # a page without any translation has only one picture to give: the clean one, without a suffix
                    files = page_exports(paths, os.path.splitext(page.file_name)[0],
                                         content if translated else "clean", fmt, quality, data)
                    for name, blob in files:
                        if archive is not None:
                            archive.writestr(f"{chapter.number}/{name}", blob)
                        else:
                            out_dir = os.path.join(target_root, chapter.number)
                            os.makedirs(out_dir, exist_ok=True)
                            with open(os.path.join(out_dir, name), "wb") as f:
                                f.write(blob)
                    written += 1
                    if translated and content != "clean":
                        stamped.append(page.id)
                except FileNotFoundError:
                    skipped += 1
                finally:
                    job["done_bytes"] += 1
        finally:
            if archive is not None:
                archive.close()
        where = zip_path or target_root
        record_exports(project, stamped, where)
        notes = ([f"{skipped} without translation"] if skipped else []) + ([f"{unreviewed} not reviewed"] if unreviewed else [])
        return {"message": f"Exported {written} page(s) to {where}" + (f" (skipped: {', '.join(notes)})" if notes else ""),
                "written": written, "skipped": skipped, "unreviewed": unreviewed, "path": where}
    finally:
        db.close()
