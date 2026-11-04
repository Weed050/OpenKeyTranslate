
# backend/scripts/rebuild_clean_images.py

"""
Rebuild the clean (text-erased) page images of a project from the raw files + the saved line data - the editor's
"Rebuild image" button for many pages at once. No OCR, no translation.

Needed once after the inpainting fix of 2026-10-28 (utils/inpainting.build_erase_mask): the outline protection ran
over the whole find_interior search window, so on a manga page only the first white bubble was erased and the other
bubbles kept their text (captions got a grey, bold-looking halo). Pages processed before the fix still show that.

What it touches:
  - processed/<chapter>/ocr_<page>_inpainted.png - rewritten from raw/ (the old image is simply derived data;
    a copy is kept as <name>.png.bak unless --no-backup).
  - The page JSON is NOT written (bubbles, translations and edits stay exactly as they are), so it is safe to run
    while the editor is open; reload the page in the editor afterwards.
Pages without line data ("lines" in the JSON, i.e. processed by a very old version) are listed and skipped - use
Re-OCR on those.

Default is a DRY RUN that only reports.

Usage:
    cd backend
    python scripts/rebuild_clean_images.py --project "Nihonkoku Shoukan"
    python scripts/rebuild_clean_images.py --project "Nihonkoku Shoukan" --chapter Chapter_1 --apply
    python scripts/rebuild_clean_images.py --all --apply
"""

import argparse
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.database import SessionLocal
from models.models import Chapter, Page, Project
from services.inpaint_service import reinpaint_from_json
from services.page_export import load_page_json, page_paths


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--project", help="project name (as in the app)")
    ap.add_argument("--all", action="store_true", help="every project")
    ap.add_argument("--chapter", help="only this chapter folder, e.g. Chapter_1")
    ap.add_argument("--apply", action="store_true", help="really rewrite the images (default: dry run)")
    ap.add_argument("--no-backup", action="store_true", help="do not keep the old image as .png.bak")
    args = ap.parse_args()
    if not args.project and not args.all:
        ap.error("give --project NAME or --all")

    db = SessionLocal()
    try:
        query = db.query(Page).join(Chapter).join(Project).filter(Page.status != "excluded")
        if args.project:
            query = query.filter(Project.name == args.project)
        if args.chapter:
            query = query.filter(Chapter.number == args.chapter)
        pages = query.order_by(Project.id, Chapter.id, Page.order).all()
        if not pages:
            print("No pages found (check the project / chapter name).")
            return 1

        done, no_lines, missing = 0, [], []
        for page in pages:
            paths = page_paths(page)
            label = f"{page.chapter.project.name}/{page.chapter.number}/{page.file_name}"
            if not (os.path.exists(paths["json"]) and os.path.exists(paths["raw"])):
                missing.append(label)
                continue
            ocr_data = load_page_json(paths["json"])
            if ocr_data.get("lines") is None:
                no_lines.append(label)
                continue
            if args.apply:
                if not args.no_backup and os.path.exists(paths["image"]) and not os.path.exists(paths["image"] + ".bak"):
                    shutil.copy2(paths["image"], paths["image"] + ".bak")
                reinpaint_from_json(paths, ocr_data)       # writes the image only; ocr_data is NOT saved
            done += 1

        verb = "rebuilt" if args.apply else "would rebuild"
        print(f"[REBUILD] {verb} {done} page image(s).")
        if no_lines:
            print(f"[REBUILD] {len(no_lines)} page(s) have no line data - run Re-OCR on them, e.g. {no_lines[0]}")
        if missing:
            print(f"[REBUILD] {len(missing)} page(s) not processed yet / raw file missing, e.g. {missing[0]}")
        if not args.apply:
            print("[REBUILD] dry run - add --apply to rewrite the images.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
