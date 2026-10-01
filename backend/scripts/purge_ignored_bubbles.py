
# backend/scripts/purge_ignored_bubbles.py

"""
One-off cleanup: remove already-saved bubbles that match the ignore
patterns (see utils/ignore_filter.py). The OCR-time filter only affects
pages processed AFTER it was enabled - pages already processed keep their
watermark bubbles in the saved *_ocr.json (shown in the editor, translated,
logged) until this script removes them.

What it touches:
  - each page's saved JSON: matching bubbles are removed. Every changed file
    is first copied to <name>.json.bak (not overwritten if a .bak exists).
    The page image is NOT touched - the text was already erased from it.
  - with --purge-logs: matching TranslationLog rows (so watermark
    translations stop skewing the log statistics). The database file is
    copied to <db>.purge.bak first.

It does NOT touch correction memory or the glossary.

Default is a DRY RUN that only reports. Close the app before --apply.

Usage:
    cd backend
    python scripts/purge_ignored_bubbles.py
    python scripts/purge_ignored_bubbles.py --project "project_name"
    python scripts/purge_ignored_bubbles.py --apply
    python scripts/purge_ignored_bubbles.py --apply --purge-logs
"""

import argparse
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.config import DATABASE_PATH, OCR_IGNORE_PATTERNS
from core.database import SessionLocal
from models.models import Chapter, Page, Project, TranslationLog
from services.page_export import page_paths
from utils.ignore_filter import compile_patterns, is_ignored_text


def purge(db, compiled, apply=False, purge_logs=False, project_name=None) -> dict:
    """Core logic (separate from main() so it can be tested with any session). Returns a stats dict."""
    stats = {"files_changed": 0, "bubbles_removed": 0, "log_rows_removed": 0}

    page_query = (
        db.query(Page)
        .join(Chapter, Page.chapter_id == Chapter.id)
        .join(Project, Chapter.project_id == Project.id)
    )
    if project_name:
        page_query = page_query.filter(Project.name == project_name)

    for page in page_query.all():
        json_path = page_paths(page)["json"]
        if not os.path.exists(json_path):
            continue

        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        keep, drop = [], []
        for bubble in data.get("bubbles", []):
            (drop if is_ignored_text(bubble.get("text", ""), compiled) else keep).append(bubble)
        if not drop:
            continue

        for bubble in drop:
            print(f"  page {page.id} ({page.file_name}): {bubble.get('bubble_id')} {bubble.get('text')!r}")
        stats["files_changed"] += 1
        stats["bubbles_removed"] += len(drop)

        if apply:
            backup = json_path + ".bak"
            if not os.path.exists(backup):
                shutil.copy2(json_path, backup)
            data["bubbles"] = keep
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

    if purge_logs:
        log_query = (
            db.query(TranslationLog)
            .join(Page, TranslationLog.page_id == Page.id)
            .join(Chapter, Page.chapter_id == Chapter.id)
            .join(Project, Chapter.project_id == Project.id)
        )
        if project_name:
            log_query = log_query.filter(Project.name == project_name)
        doomed = [row for row in log_query.all() if is_ignored_text(row.source_text, compiled)]
        stats["log_rows_removed"] = len(doomed)
        if apply:
            for row in doomed:
                db.delete(row)
            db.commit()

    return stats


def main():
    parser = argparse.ArgumentParser(description="Remove saved bubbles/log rows that match the OCR ignore patterns.")
    parser.add_argument("--apply", action="store_true", help="actually write changes (default: dry run)")
    parser.add_argument("--purge-logs", action="store_true", help="also delete matching TranslationLog rows")
    parser.add_argument("--project", help="limit to one project (exact name)")
    args = parser.parse_args()

    compiled = compile_patterns(OCR_IGNORE_PATTERNS)
    if not compiled:
        print("No ocr_ignore_patterns configured - nothing to do.")
        return

    print(f"Patterns: {list(OCR_IGNORE_PATTERNS)}")
    print("MODE: " + ("APPLY" if args.apply else "DRY RUN (add --apply to write changes)"))

    if args.apply and args.purge_logs:
        backup = DATABASE_PATH + ".purge.bak"
        if not os.path.exists(backup):
            shutil.copy2(DATABASE_PATH, backup)
            print(f"Database backed up to {backup}")

    db = SessionLocal()
    try:
        stats = purge(db, compiled, apply=args.apply, purge_logs=args.purge_logs, project_name=args.project)
    finally:
        db.close()

    verb = "Removed" if args.apply else "Would remove"
    print(f"\n{verb}: {stats['bubbles_removed']} bubble(s) from {stats['files_changed']} file(s)"
          + (f", {stats['log_rows_removed']} log row(s)" if args.purge_logs else ""))
    if not args.apply:
        print("Dry run only - nothing was changed.")


if __name__ == "__main__":
    main()