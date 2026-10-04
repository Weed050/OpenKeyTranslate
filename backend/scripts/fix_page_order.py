
# backend/scripts/fix_page_order.py

"""
Repair the PAGE ORDER of already-imported chapters from the ORIGINAL source folder.

Problem: the old import numbered pages page_001.. by file MTIME first. For scraped chapters mtime is
download-completion noise, so raw/Chapter_X/page_001.. ended up in a different order than the source
names (page_01.jpg .. page_28.jpg).

What this does (no guessing):
  - sha1 of every raw/<chapter>/page_NNN file is matched to a file in the source chapter folder
    (raw pages are exact copies of the source pages),
  - the desired order = order of those source files (utils.chapter_labels.order_names: by NAME when the names
    are a clean sequence like page_01..page_28, else by mtime; override with --mode),
  - ONLY the DB column pages."order" is rewritten. Files are NOT renamed or moved, page ids, statuses,
    OCR json, inpainted images, corrections, logs are untouched -> safe.
    (The editor sorts pages by "order", so prev/next + the page list follow the new order immediately.)

WHAT CHANGES IN THE DB (nothing else is ever touched): pages."order"  - and with --rename also pages.file_name.
Page ids, statuses, translation_logs.page_id, corrections, glossary, OCR JSON content: unchanged.

--rename  ALSO renames the files so the NAMES match the new order (page_001.. = reading order again):
          raw/<chapter>/page_NNN.ext, processed/<chapter>/page_NNN_ocr.json, ocr_page_NNN_inpainted.png
          (+ _debug / _translated if present) and pages.file_name. Two-phase rename (cycle-safe), the DB is
          updated in one transaction, any failure rolls the file renames back.

Default is a DRY RUN. Close the app before --apply. The DB is copied to <db>.pageorder.<timestamp>.bak first.

Usage:
    cd backend
    python scripts/fix_page_order.py --project "Nihonkoku Shoukan" --source "D:\\manga_scraped\\Nihonkoku Shoukan"
    python scripts/fix_page_order.py --project "Nihonkoku Shoukan" --source "..." --chapter Chapter_2 --apply
    python scripts/fix_page_order.py --project "X" --source "..." --mode name        # force name order
    python scripts/fix_page_order.py --project "X" --source "..." --rename --apply   # names follow the order too
"""

import argparse
import os
import shutil
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils.chapter_labels import list_images, order_names, scan_source, sha1_of_file  # noqa: E402


def match_source(raw_hashes: dict, sources: list[dict]):
    """Pick the source chapter folder sharing the most identical pages. -> (source|None, shared, hash->name map)."""
    best, best_shared, best_map = None, 0, {}
    for s in sources:
        if "hashes" not in s:
            s["hashes"] = {sha1_of_file(os.path.join(s["folder"], n)): n for n in list_images(s["folder"])}
        shared = len(set(raw_hashes.values()) & set(s["hashes"]))
        if shared > best_shared:
            best, best_shared, best_map = s, shared, s["hashes"]
    return best, best_shared, best_map


def plan_chapter(pages: list[dict], raw_dir: str, sources: list[dict], mode: str):
    """pages: [{"id","file_name","order","status"}]. Returns (plan rows, problems)."""
    problems = []
    raw_hashes = {}
    for p in pages:
        path = os.path.join(raw_dir, p["file_name"])
        if os.path.exists(path):
            raw_hashes[p["id"]] = sha1_of_file(path)
        else:
            problems.append(f"raw file missing: {p['file_name']} (page id {p['id']}) - left at the end")

    src, shared, hash_to_name = match_source(raw_hashes, sources)
    if src is None:
        return [], problems + ["no source folder shares any page with this chapter"]
    if shared < len(raw_hashes):
        problems.append(f"only {shared}/{len(raw_hashes)} pages found in source '{src['name']}' (rest go to the end)")

    src_names = list_images(src["folder"])
    ordered = order_names(src["folder"], src_names, mode)
    rank = {n: i for i, n in enumerate(ordered["names"])}

    def sort_key(p):
        name = hash_to_name.get(raw_hashes.get(p["id"]))
        return (0, rank[name]) if name in rank else (1, p["order"] or 0)

    rows = []
    for new_order, p in enumerate(sorted(pages, key=sort_key), start=1):
        rows.append({**p, "new_order": new_order, "source_name": hash_to_name.get(raw_hashes.get(p["id"]), "-"),
                     "source_folder": src["name"], "method": ordered["method"]})
    return rows, problems


PROCESSED_TEMPLATES = ("{b}_ocr.json", "ocr_{b}_inpainted.png", "ocr_{b}_debug.png", "ocr_{b}_translated.png")


def plan_renames(rows: list[dict], raw_dir: str, processed_dir: str):
    """rows from plan_chapter. Sets r["new_file_name"] and returns (ops, problems); ops = [(old, tmp, new)]."""
    ops, problems = [], []
    for r in rows:
        old_name = r["file_name"]
        ext = os.path.splitext(old_name)[1]
        new_name = f"page_{r['new_order']:03d}{ext}"
        r["new_file_name"] = new_name
        if new_name == old_name:
            continue
        ob, nb = os.path.splitext(old_name)[0], os.path.splitext(new_name)[0]
        pairs = [(os.path.join(raw_dir, old_name), os.path.join(raw_dir, new_name))]
        pairs += [(os.path.join(processed_dir, t.format(b=ob)), os.path.join(processed_dir, t.format(b=nb)))
                  for t in PROCESSED_TEMPLATES]
        for old, new in pairs:
            if os.path.exists(old):
                ops.append((old, f"{old}.__tmp_{r['id']}", new))
    sources = {o for o, _, _ in ops}
    for _, _, new in ops:
        if os.path.exists(new) and new not in sources:
            problems.append(f"target already exists and is not part of this rename: {new}")
    return ops, problems


def run_renames(ops):
    """Phase 1: old -> tmp, phase 2: tmp -> new. On any error everything done so far is reverted."""
    done1, done2 = [], []
    try:
        for old, tmp, _ in ops:
            os.rename(old, tmp)
            done1.append((old, tmp))
        for _, tmp, new in ops:
            os.rename(tmp, new)
            done2.append((tmp, new))
    except Exception:
        for tmp, new in reversed(done2):
            os.rename(new, tmp)
        for old, tmp in reversed(done1):
            os.rename(tmp, old)
        raise


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1], formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", required=True, help="exact project name")
    ap.add_argument("--source", required=True, help="ORIGINAL scans folder (a chapter folder or the parent of many)")
    ap.add_argument("--chapter", help="only this chapter label, e.g. Chapter_2 (default: all)")
    ap.add_argument("--mode", choices=["auto", "name", "mtime"], default="auto")
    ap.add_argument("--db", help="database file (default: from core.config)")
    ap.add_argument("--rename", action="store_true", help="also rename files + pages.file_name so names follow the new order")
    ap.add_argument("--apply", action="store_true", help="write changes (default: dry run)")
    args = ap.parse_args()

    db_path = args.db
    if not db_path:
        from core.config import DATABASE_PATH
        db_path = DATABASE_PATH
    if not os.path.exists(db_path):
        sys.exit(f"Database not found: {db_path}")
    if not os.path.isdir(args.source):
        sys.exit(f"Source folder not found: {args.source}")

    con = sqlite3.connect(db_path)
    cur = con.cursor()
    cur.execute("SELECT id, workspace_path FROM projects WHERE name = ?", (args.project,))
    row = cur.fetchone()
    if not row:
        sys.exit(f"Project '{args.project}' not found.")
    project_id, workspace = row

    q = "SELECT id, number FROM chapters WHERE project_id = ?"
    params = [project_id]
    if args.chapter:
        q += " AND number = ?"
        params.append(args.chapter)
    cur.execute(q, params)
    chapters = cur.fetchall()
    if not chapters:
        sys.exit("No matching chapters in DB.")

    sources, skipped = scan_source(args.source)
    if not sources:
        sys.exit("No image folders found in --source.")

    updates, total_changed = [], 0
    all_ops, name_updates, rename_problems = [], [], []
    for chapter_id, number in chapters:
        cur.execute('SELECT id, file_name, "order", status FROM pages WHERE chapter_id = ?', (chapter_id,))
        pages = [{"id": r[0], "file_name": r[1], "order": r[2], "status": r[3]} for r in cur.fetchall()]
        rows, problems = plan_chapter(pages, os.path.join(workspace, "raw", number), sources, args.mode)
        print(f"\n=== {number}: {len(pages)} pages" + (f" <- source '{rows[0]['source_folder']}' ordered by {rows[0]['method']}" if rows else ""))
        for pr in problems:
            print(f"  PROBLEM: {pr}")
        changed = [r for r in rows if r["new_order"] != r["order"]]
        total_changed += len(changed)
        print(f"  {len(changed)} of {len(rows)} pages change position")
        for r in sorted(rows, key=lambda r: r["new_order"])[:60]:
            mark = "*" if r["new_order"] != r["order"] else " "
            print(f"  {mark} new {r['new_order']:>3} (was {r['order']!s:>3})  {r['file_name']:<14} = source {r['source_name']}")
        updates.extend((r["new_order"], r["id"]) for r in changed)
        if args.rename and rows:
            ops, probs = plan_renames(rows, os.path.join(workspace, "raw", number), os.path.join(workspace, "processed", number))
            all_ops.extend(ops)
            rename_problems.extend(f"{number}: {p}" for p in probs)
            name_updates.extend((r["new_file_name"], r["new_order"], r["id"]) for r in rows if r["new_file_name"] != r["file_name"])
            print(f"  --rename: {sum(1 for r in rows if r['new_file_name'] != r['file_name'])} page(s) renamed, {len(ops)} file(s) moved")

    for p in rename_problems:
        print(f"PROBLEM: {p}")
    if rename_problems:
        sys.exit("\nAborting: fix the problems above first (nothing was changed).")
    if not args.apply:
        print(f"\nDRY RUN - {total_changed} page(s) would be re-ordered" + (f", {len(all_ops)} file(s) renamed" if args.rename else "")
              + ". Add --apply to write.")
        return
    if not updates:
        print("\nNothing to change.")
        return

    backup = f"{db_path}.pageorder.{time.strftime('%Y%m%d-%H%M%S')}.bak"
    shutil.copy2(db_path, backup)
    print(f"\nDatabase backed up to {backup}")
    if args.rename:
        run_renames(all_ops)
        try:
            cur.executemany('UPDATE pages SET file_name = ?, "order" = ? WHERE id = ?', name_updates)
            cur.executemany('UPDATE pages SET "order" = ? WHERE id = ?', [u for u in updates if u[1] not in {n[2] for n in name_updates}])
            con.commit()
        except Exception:
            con.rollback()
            for old, tmp, new in reversed(all_ops):      # DB failed: put the files back
                os.rename(new, tmp)
            for old, tmp, new in reversed(all_ops):
                os.rename(tmp, old)
            raise
        print(f"Done: {len(updates)} page(s) re-ordered, {len(all_ops)} file(s) renamed, DB updated.")
    else:
        cur.executemany('UPDATE pages SET "order" = ? WHERE id = ?', updates)
        con.commit()
        print(f"Done: {len(updates)} page(s) re-ordered (files untouched). Just reload the page list.")
    con.close()


if __name__ == "__main__":
    main()
