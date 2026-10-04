
# backend/scripts/apply_textfix_existing.py

"""
Apply the post-OCR text cleanup (utils/ocr_textfix.py) to pages that were processed BEFORE it existed.

New pages get the cleanup automatically (and the raw OCR string is kept in bubble["text_raw"]). Old pages keep their
noisy text ("SQUAD- RON", "LNIT") until this script runs.

WHAT IT TOUCHES
  page JSON  bubbles[].text  -> cleaned; the previous string goes to text_raw (once). Translations are NOT changed.
             Every changed JSON is first copied to <name>.json.bak (not overwritten if a .bak exists).
  --corrections   also corrections.source_text -> cleaned + the embedding is recomputed, so old corrections keep
             matching the (now cleaned) text of new bubbles. DB copied to <db>.textfix.<timestamp>.bak first.
WHAT IT NEVER TOUCHES
  translation_logs (historical record of what was actually sent), glossary, page images, statuses, page order.
  Number / symbol-only bubbles are NOT flagged as skipped here (their text is already erased from the image of an old
  page); use Re-OCR in the editor for a page where you want that.

Stutters ("TH-THEN", "W-WHAT") and real compounds are left alone (see ocr_textfix.py for the rules). Names listed in
the project's glossary are protected.

Default is a DRY RUN that prints every change. Close the app before --apply.

Usage:
    cd backend
    python scripts/apply_textfix_existing.py --project "Nihonkoku Shoukan"
    python scripts/apply_textfix_existing.py --project "Nihonkoku Shoukan" --apply --corrections
"""

import argparse
import json
import os
import shutil
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils.ocr_textfix import fix_ocr_text  # noqa: E402


def run(db_path: str, project_name: str, apply: bool = False, fix_corrections: bool = False, embed_fn=None, out=print) -> dict:
    con = sqlite3.connect(db_path)
    cur = con.cursor()
    row = cur.execute("SELECT id, workspace_path FROM projects WHERE name = ?", (project_name,)).fetchone()
    if not row:
        raise SystemExit(f"Project '{project_name}' not found.")
    project_id, workspace = row
    protected = [r[0] for r in cur.execute("SELECT source_term FROM glossary_terms WHERE project_id = ?", (project_id,))]
    stats = {"files": 0, "bubbles": 0, "corrections": 0}

    pages = cur.execute(
        """SELECT p.id, p.file_name, c.number FROM pages p JOIN chapters c ON c.id = p.chapter_id
           WHERE c.project_id = ? AND p.status = 'processed'""", (project_id,)).fetchall()
    for page_id, file_name, chapter in pages:
        json_path = os.path.join(workspace, "processed", chapter, f"{os.path.splitext(file_name)[0]}_ocr.json")
        if not os.path.exists(json_path):
            continue
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        changed = 0
        for b in data.get("bubbles", []):
            if b.get("text_raw"):
                continue                                  # already cleaned or edited by the user
            fixed = fix_ocr_text(b["text"], protected)
            if fixed != b["text"]:
                out(f"  {chapter}/{file_name} {b['bubble_id']}: {b['text']!r}\n      -> {fixed!r}")
                b["text_raw"], b["text"] = b["text"], fixed
                changed += 1
        if changed:
            stats["files"] += 1
            stats["bubbles"] += changed
            if apply:
                if not os.path.exists(json_path + ".bak"):
                    shutil.copy2(json_path, json_path + ".bak")
                with open(json_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)

    if fix_corrections:
        todo = []
        for cid, src in cur.execute("SELECT id, source_text FROM corrections WHERE project_id = ?", (project_id,)).fetchall():
            fixed = fix_ocr_text(src, protected)
            if fixed != src:
                out(f"  correction #{cid}: {src!r}\n      -> {fixed!r}")
                todo.append((cid, fixed))
        stats["corrections"] = len(todo)
        if apply and todo:
            if embed_fn is None:
                from services.memory_service import embed_text, encode_embedding
                embed_fn = lambda t: encode_embedding(embed_text(t))
            backup = f"{db_path}.textfix.{time.strftime('%Y%m%d-%H%M%S')}.bak"
            shutil.copy2(db_path, backup)
            out(f"Database backed up to {backup}")
            cur.executemany("UPDATE corrections SET source_text = ?, embedding = ? WHERE id = ?",
                            [(fixed, embed_fn(fixed), cid) for cid, fixed in todo])
            con.commit()
    con.close()
    return stats


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1], formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", required=True, help="exact project name")
    ap.add_argument("--corrections", action="store_true", help="also fix corrections.source_text (+ re-embed)")
    ap.add_argument("--db", help="database file (default: from core.config)")
    ap.add_argument("--apply", action="store_true", help="write changes (default: dry run)")
    args = ap.parse_args()
    db_path = args.db
    if not db_path:
        from core.config import DATABASE_PATH
        db_path = DATABASE_PATH
    if not os.path.exists(db_path):
        sys.exit(f"Database not found: {db_path}")
    stats = run(db_path, args.project, args.apply, args.corrections)
    verb = "Changed" if args.apply else "Would change"
    print(f"\n{verb}: {stats['bubbles']} bubble(s) in {stats['files']} page file(s)"
          + (f", {stats['corrections']} correction(s)" if args.corrections else ""))
    if not args.apply:
        print("Dry run only - nothing was changed. Add --apply to write.")


if __name__ == "__main__":
    main()
