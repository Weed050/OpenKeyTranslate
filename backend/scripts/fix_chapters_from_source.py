
# backend/scripts/fix_chapters_from_source.py

"""
Repair chapter numbering of an existing project from the ORIGINAL source folder.
Replaces scripts/fix_chapter_order.py (do not run that one again).

What went wrong before
  1. Old import sorted source folders alphabetically (chapter_10 < chapter_2) and
     numbered them with a counter, ignoring their real names (13.5, Epilog, ...).
  2. fix_chapter_order.py renamed the DB rows one after another with
     "WHERE number=?", so rows renamed in step N were renamed AGAIN in step N+k
     (2 -> 10 -> 6). Several rows ended up with the same number.
  3. "Add chapters" re-imported a whole parent folder, cloning chapters that
     already existed.

How this script decides (no guessing, no hand-written mapping table)
  - It reads the source folder you point it at and derives each chapter's real
    label from the folder name (chapter_13_5 -> Chapter_13.5).
  - It fingerprints the first + last page (sha1) of every source folder and of
    every raw/<folder> on disk. Same fingerprint = same chapter, whatever the
    folders are called now. Raw pages are exact copies of the source pages.
  - Pairing DB rows -> source folders:
      --pair number        (default) via the row's own raw/<number> folder.
                           Works when DB numbers are unique (e.g. clone imports).
                           Duplicate rows (same content twice) are detected; the
                           one with more processed pages is kept, the other is
                           moved to <workspace>/_removed_duplicates/.
      --pair order-alpha   DB rows by id <-> source folders sorted alphabetically.
                           Reconstructs the OLD buggy import order; use it when DB
                           numbers are corrupted (duplicate numbers). Checked
                           against page counts; aborts on any mismatch.
      --pair order-natural same, natural sort (projects imported after the fix).
  - Then it renames raw/ + processed/ folders (two-phase, cycle-safe) and updates
    the DB rows BY ID (number, title, raw_path, processed_path). Page rows, page
    ids, corrections, glossary and translation logs are untouched.

  - Processed work travels WITH its chapter: raw/<x> and processed/<x> are renamed
    together and the page rows stay attached to their chapter row, so OCR output,
    inpainted images, translations and statuses end up under the right label.
  - Page statuses are then re-synced from what really exists on disk
    (processed/<label>/<page>_ocr.json + ocr_<page>_inpainted.png). Pages that were
    "processed" in the DB while the broken numbering pointed them at another folder
    are set back to pending (their outputs, if any, belong to the folder they were
    written into and are picked up there). Use --no-sync-status to skip this.
  - Safety check for the order-* pairings: a chapter whose "processed" pages have NO
    output in the paired folder aborts the run (the pairing is probably wrong - the dry-run
    table shows which folder each source chapter really sits in). If you know the cause is
    pages processed under the broken numbering, add --allow-status-reset.

Default is a DRY RUN. Close the app first. The database is copied to
<db>.chapterfix.<timestamp>.bak before --apply changes anything.

Usage:
    cd backend
    python scripts/fix_chapters_from_source.py --project "NAME" --source "D:\\manga_scraped\\NAME"
    python scripts/fix_chapters_from_source.py --project "NAME" --source "..." --pair order-alpha --apply
"""

import argparse
import os
import shutil
import sqlite3
import sys
import time
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils.chapter_labels import (  # noqa: E402  (pure stdlib helpers, safe to import standalone)
    chapter_fingerprint, derive_chapter_label, list_images, natural_sort_key, scan_source, sha1_of_file,
)


def load_rows(cur, project_id):
    cur.execute(
        """SELECT c.id, c.number,
                  (SELECT COUNT(*) FROM pages p WHERE p.chapter_id = c.id),
                  (SELECT COUNT(*) FROM pages p WHERE p.chapter_id = c.id AND p.status = 'processed')
           FROM chapters c WHERE c.project_id = ? ORDER BY c.id""",
        (project_id,),
    )
    return [{"id": r[0], "number": r[1], "pages": r[2], "processed": r[3]} for r in cur.fetchall()]


def disk_fingerprints(raw_dir):
    """{folder_name: fingerprint} for every sub-folder of raw/."""
    result = {}
    if os.path.isdir(raw_dir):
        for name in sorted(os.listdir(raw_dir), key=natural_sort_key):
            path = os.path.join(raw_dir, name)
            if os.path.isdir(path):
                result[name] = chapter_fingerprint(path)
    return result


def analyze_disk(raw_dir, disk, sources):
    """
    For every raw/<folder>: which source folder does it come from?
    Exact match = same first/last page fingerprint. If there is none, fall back to counting identical
    pages (sha1 of every file) - catches a chapter that is "almost" a source folder (pages added/removed,
    re-scraped) and tells you which source it really is.
    :return: {folder: {"pages": n, "exact": [source names], "best": source dict|None, "shared": k}}
    """
    cache = {}

    def hashes(folder):
        if folder not in cache:
            cache[folder] = {sha1_of_file(os.path.join(folder, n)) for n in list_images(folder)}
        return cache[folder]

    info = {}
    for name, fp in disk.items():
        path = os.path.join(raw_dir, name)
        entry = {"pages": len(list_images(path)), "exact": [s["name"] for s in sources if fp and s["fp"] == fp],
                 "best": None, "shared": 0, "all_best": []}
        if not entry["exact"] and entry["pages"]:
            mine = hashes(path)
            for s in sources:
                shared = len(mine & hashes(s["folder"]))
                if shared > entry["shared"]:
                    entry["best"], entry["shared"], entry["all_best"] = s, shared, [s]
                elif shared and shared == entry["shared"]:
                    entry["all_best"].append(s)  # several source folders hold the same pages (scrape duplicate?)
        info[name] = entry
    return info


def print_disk_report(info):
    print("\nDISK -> SOURCE (what each raw/ folder really contains)")
    for name, e in info.items():
        if e["exact"]:
            print(f"  raw/{name:<16} {e['pages']:>3} pages = source '{', '.join(e['exact'])}'")
        elif e["best"]:
            names = ", ".join(f"'{b['name']}'" for b in e["all_best"])
            tie = "  <-- SEVERAL source folders contain these same pages!" if len(e["all_best"]) > 1 else ""
            print(f"  raw/{name:<16} {e['pages']:>3} pages: NO exact match; shares {e['shared']} page(s) with "
                  f"source {names}{tie}")
        else:
            print(f"  raw/{name:<16} {e['pages']:>3} pages: NO match with any source folder")


def resolve_order_candidates(rows, ordered, fp_to_disk, disk_info, processed_dir, processed_pages):
    """
    Order modes: row i <-> ordered[i]. For each row collect the raw/ folders that hold its source's pages
    (exact fingerprint, else >=90% identical pages). Several candidates (identical chapters, or two folders
    with the same content) are resolved in rounds:
      1. a row whose only free candidate is X takes X
      2. otherwise the candidate that holds the row's own processed outputs wins
      3. otherwise the candidate named like the row's current DB number
    :return: ({row_id: folder or None}, [notes per row id])
    """
    cands = {}
    for row, src in zip(rows, ordered):
        found = list(fp_to_disk.get(src["fp"], []))
        if not found and disk_info:
            found = [n for n, inf in disk_info.items()
                     if any(b is src for b in inf["all_best"]) and inf["shared"] >= 0.9 * max(len(src["images"]), 1)]
        cands[row["id"]] = found

    def evidence(row, folder):
        return sum(1 for fn in processed_pages.get(row["id"], []) if page_outputs_exist(processed_dir, folder, fn))

    chosen, used, notes = {}, set(), {}
    progress = True
    while progress:
        progress = False
        for row in rows:
            if row["id"] in chosen:
                continue
            free = [c for c in cands[row["id"]] if c not in used]
            pick, why = None, ""
            if len(free) == 1:
                pick = free[0]
                why = "" if len(cands[row["id"]]) == 1 else "other candidate(s) already taken"
            elif len(free) > 1:
                scored = sorted(((evidence(row, c), c) for c in free), reverse=True)
                if scored[0][0] > 0 and scored[0][0] > scored[1][0]:
                    pick, why = scored[0][1], f"chosen because it holds this chapter's {scored[0][0]} processed output(s)"
                else:
                    named = [c for c in free if c == row["number"]]
                    if len(named) == 1:
                        pick, why = named[0], "chosen: same name as the current DB number"
            if pick:
                chosen[row["id"]] = pick
                used.add(pick)
                notes[row["id"]] = why
                progress = True
    return chosen, notes, cands


def build_plan(rows, sources, disk, raw_dir, mode, disk_info=None, processed_dir=None, processed_pages=None):
    """Returns (plan_rows, deletions, problems)."""
    problems = []
    for s in sources:
        s["fp"] = chapter_fingerprint(s["folder"])

    if mode == "number":
        dup_numbers = [n for n, c in Counter(r["number"] for r in rows).items() if c > 1]
        if dup_numbers:
            problems.append(f"DB has duplicate chapter numbers {dup_numbers}: use --pair order-alpha (or order-natural).")
    else:
        ordered = sorted(sources, key=(lambda s: s["name"]) if mode == "order-alpha"
                         else (lambda s: natural_sort_key(s["name"])))
        if len(ordered) != len(rows):
            problems.append(f"{len(rows)} DB chapters but {len(ordered)} source folders - order pairing impossible.")

    fp_to_disk = defaultdict(list)
    for name, fp in disk.items():
        if fp:
            fp_to_disk[fp].append(name)

    chosen, notes, cands = {}, {}, {}
    if mode != "number" and len(ordered) == len(rows):
        chosen, notes, cands = resolve_order_candidates(
            rows, ordered, fp_to_disk, disk_info, processed_dir, processed_pages or {})

    plan = []
    for i, row in enumerate(rows):
        entry = {**row, "src": None, "label": None, "disk": None, "note": ""}
        if mode == "number":
            fp = disk.get(row["number"])
            if fp is None:
                entry["note"] = f"raw/{row['number']} missing or empty"
            else:
                match = [s for s in sources if s["fp"] == fp]
                if match:
                    entry["src"], entry["disk"] = match[0], row["number"]
                else:
                    entry["note"] = "no matching source folder (left untouched)"
        elif len(ordered) == len(rows):
            src = ordered[i]
            entry["src"] = src
            if src["fp"] is None:
                problems.append(f"source '{src['name']}' has no images")
            else:
                folder = chosen.get(row["id"])
                if folder:
                    entry["disk"] = folder
                    entry["note"] = notes.get(row["id"], "")
                else:
                    entry["note"] = f"disk folder for '{src['name']}': {len(cands[row['id']])} candidate(s) {cands[row['id']]}, none could be chosen"
                    problems.append(entry["note"])
            if len(src["images"]) != row["pages"]:
                problems.append(f"id {row['id']}: DB has {row['pages']} pages, source '{src['name']}' has {len(src['images'])}")
        if entry["src"] is not None:
            entry["label"] = derive_chapter_label(entry["src"]["name"])
        plan.append(entry)

    # duplicates (number mode): several rows -> same source folder
    deletions = []
    by_src = defaultdict(list)
    for e in plan:
        if e["src"] is not None:
            by_src[e["src"]["folder"]].append(e)
    for group in by_src.values():
        if len(group) > 1:
            group.sort(key=lambda e: (-e["processed"], e["id"]))
            for extra in group[1:]:
                extra["note"] = f"DUPLICATE of id {group[0]['id']} -> removed"
                deletions.append(extra)
    doomed = {e["id"] for e in deletions}
    kept = [e for e in plan if e["id"] not in doomed]

    # label collisions among rows that stay
    taken = Counter(e["label"] for e in kept if e["label"])
    for label, count in taken.items():
        if count > 1:
            problems.append(f"label '{label}' would be used {count}x")
    untouched = {e["number"] for e in kept if e["label"] is None}
    for e in kept:
        if e["label"] and e["label"] in untouched:
            problems.append(f"label '{e['label']}' collides with an untouched chapter of the same name")
    return plan, deletions, problems


def page_outputs_exist(processed_dir, folder, file_name):
    """Same file layout as services/page_export.page_paths."""
    base = os.path.splitext(file_name)[0]
    d = os.path.join(processed_dir, folder)
    return (os.path.exists(os.path.join(d, f"{base}_ocr.json"))
            and os.path.exists(os.path.join(d, f"ocr_{base}_inpainted.png")))


def check_status_sync(cur, plan, deletions, processed_dir, mode, allow_reset):
    """
    Compare page statuses in the DB with the processed/ outputs of the folder each chapter row
    will end up in. Returns (changes, problems, warnings); changes = {"to_pending": [ids], "to_processed": [ids]}.
    """
    doomed = {e["id"] for e in deletions}
    changes = {"to_pending": [], "to_processed": [], "suspect_log_pages": []}
    problems, warnings = [], []
    for e in plan:
        if e["id"] in doomed:
            continue
        folder = e["disk"] if (e["label"] and e["disk"]) else e["number"]
        cur.execute("SELECT id, file_name, status FROM pages WHERE chapter_id = ?", (e["id"],))
        pages = cur.fetchall()
        marked = [p for p in pages if p[2] == "processed"]
        marked_with_output = 0
        for pid, file_name, status in pages:
            has_output = page_outputs_exist(processed_dir, folder, file_name)
            if status == "processed" and has_output:
                marked_with_output += 1
            if status == "excluded":
                continue
            if has_output and status != "processed":
                changes["to_processed"].append(pid)
            elif not has_output and status in ("processed", "queued", "processing"):
                changes["to_pending"].append(pid)
                if status == "processed":
                    changes["suspect_log_pages"].append(pid)
        if marked and marked_with_output == 0:
            msg = (f"id {e['id']} ({e['number']}): {len(marked)} page(s) marked processed but folder '{folder}' "
                   f"has NONE of their outputs")
            if mode != "number" and not allow_reset:
                problems.append(msg + f" - pairing with source '{e['src']['name'] if e['src'] else '?'}' may be wrong "
                                      f"(or they were processed under the broken numbering: then add --allow-status-reset)")
            else:
                warnings.append(msg + " -> will be reset to pending")
        elif marked and marked_with_output < len(marked):
            warnings.append(f"id {e['id']} ({e['number']}): {len(marked) - marked_with_output} of {len(marked)} processed page(s) "
                            f"have no output in '{folder}' -> reset to pending")
    return changes, problems, warnings


def print_plan(plan):
    print(f"\n{'id':>4} | {'DB number':<14} | {'pages(done)':<11} | {'source folder':<38} | {'-> label':<16} | {'disk now':<14} | note")
    print("-" * 130)
    for e in plan:
        src = e["src"]["name"] if e["src"] else "-"
        print(f"{e['id']:>4} | {e['number']:<14} | {e['pages']:>4}({e['processed']:>3}) | {src:<38} | "
              f"{(e['label'] or '-'):<16} | {(e['disk'] or '-'):<14} | {e['note']}")


def apply_plan(cur, con, plan, deletions, workspace, raw_dir, processed_dir, status_changes=None):
    stamp = time.strftime("%Y%m%d-%H%M%S")
    doomed = {e["id"] for e in deletions}

    # 1. duplicates: move folders away (never hard-delete), drop rows
    if deletions:
        trash_root = os.path.join(workspace, "_removed_duplicates", stamp)
        for e in deletions:
            for base in (raw_dir, processed_dir):
                src = os.path.join(base, e["disk"])
                if os.path.isdir(src):
                    dst = os.path.join(trash_root, f"{e['disk']}__id{e['id']}", os.path.basename(base))
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    shutil.move(src, dst)
                    print(f"  moved duplicate {src} -> {dst}")

    # 2. renames: pass 1 -> temp names, pass 2 -> final labels (cycle-safe)
    todo = [e for e in plan if e["id"] not in doomed and e["label"] and e["disk"] and e["disk"] != e["label"]]
    for e in todo:
        for base in (raw_dir, processed_dir):
            src = os.path.join(base, e["disk"])
            if os.path.isdir(src):
                os.rename(src, os.path.join(base, f"__tmp_{e['id']}"))
    for e in todo:
        for base in (raw_dir, processed_dir):
            tmp = os.path.join(base, f"__tmp_{e['id']}")
            if os.path.isdir(tmp):
                os.rename(tmp, os.path.join(base, e["label"]))
                print(f"  {base}: {e['disk']} -> {e['label']}")

    # 3. DB, by row id, one transaction
    try:
        for e in deletions:
            cur.execute("DELETE FROM text_blocks WHERE page_id IN (SELECT id FROM pages WHERE chapter_id = ?)", (e["id"],))
            cur.execute("DELETE FROM pages WHERE chapter_id = ?", (e["id"],))
            cur.execute("DELETE FROM chapters WHERE id = ?", (e["id"],))
        for e in plan:
            if e["id"] in doomed or not e["label"]:
                continue
            cur.execute(
                "UPDATE chapters SET number = ?, title = ?, raw_path = ?, processed_path = ? WHERE id = ?",
                (e["label"], e["label"].replace("_", " "),
                 os.path.join(raw_dir, e["label"]), os.path.join(processed_dir, e["label"]), e["id"]),
            )
        if status_changes:
            cur.executemany("UPDATE pages SET status = 'pending' WHERE id = ?", [(i,) for i in status_changes["to_pending"]])
            cur.executemany("UPDATE pages SET status = 'processed' WHERE id = ?", [(i,) for i in status_changes["to_processed"]])
        con.commit()
    except Exception:
        con.rollback()
        print("DB update FAILED and was rolled back. Folders were already renamed - restore the DB backup "
              "and rename the folders back, or re-run with the same arguments after fixing the cause.")
        raise


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1], formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", required=True, help="exact project name")
    ap.add_argument("--source", required=True, help="the ORIGINAL scans folder (parent of the chapter folders)")
    ap.add_argument("--pair", choices=["number", "order-alpha", "order-natural"], default="number")
    ap.add_argument("--db", help="database file (default: from core.config)")
    ap.add_argument("--no-sync-status", action="store_true", help="don't re-sync page statuses with processed/ outputs")
    ap.add_argument("--allow-status-reset", action="store_true",
                    help="order-* pairing: accept chapters whose 'processed' pages have no output in the paired folder")
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
    raw_dir, processed_dir = os.path.join(workspace, "raw"), os.path.join(workspace, "processed")

    rows = load_rows(cur, project_id)
    sources, skipped = scan_source(args.source)
    if not sources:
        sys.exit("No image folders found in --source.")
    print(f"Project id={project_id} ({len(rows)} chapters in DB), {len(sources)} source folders, pair mode: {args.pair}")
    for sk in skipped:
        print(f"  [note] source folder skipped (unsupported files): {sk['folder']} {sk['extensions_found']}")

    for s in sources:
        s["fp"] = chapter_fingerprint(s["folder"])
    disk = disk_fingerprints(raw_dir)
    disk_info = analyze_disk(raw_dir, disk, sources)
    processed_pages = {}
    for r in rows:
        cur.execute("SELECT file_name FROM pages WHERE chapter_id = ? AND status = 'processed'", (r["id"],))
        processed_pages[r["id"]] = [x[0] for x in cur.fetchall()]
    plan, deletions, problems = build_plan(rows, sources, disk, raw_dir, args.pair, disk_info, processed_dir, processed_pages)
    print_plan(plan)
    print_disk_report(disk_info)
    status_changes = None
    if not args.no_sync_status and not problems:
        status_changes, status_problems, status_warnings = check_status_sync(
            cur, plan, deletions, processed_dir, args.pair, args.allow_status_reset)
        problems.extend(status_problems)
        for w in status_warnings:
            print(f"WARNING: {w}")
        print(f"\nStatus sync: {len(status_changes['to_pending'])} page(s) -> pending, "
              f"{len(status_changes['to_processed'])} page(s) -> processed (outputs found on disk).")
        if status_changes["suspect_log_pages"]:
            ids = status_changes["suspect_log_pages"]
            cur.execute(f"SELECT COUNT(*) FROM translation_logs WHERE page_id IN ({','.join('?' * len(ids))})", ids)
            print(f"  Pages that were 'processed' without output: {ids[:30]}{' ...' if len(ids) > 30 else ''}")
            print(f"  {cur.fetchone()[0]} translation_logs row(s) point at them - they may describe another chapter's text "
                  f"(not deleted; exclude them from thesis statistics or delete them by page_id).")
    for p in problems:
        print(f"PROBLEM: {p}")

    if problems:
        sys.exit("\nAborting: fix the problems above first (nothing was changed).")
    if not args.apply:
        print(f"\nDRY RUN - {len(deletions)} duplicate(s) would be removed, "
              f"{sum(1 for e in plan if e['label'] and e['label'] != e['number'])} DB label(s) changed. Add --apply to write.")
        return

    backup = f"{db_path}.chapterfix.{time.strftime('%Y%m%d-%H%M%S')}.bak"
    shutil.copy2(db_path, backup)
    print(f"\nDatabase backed up to {backup}")
    apply_plan(cur, con, plan, deletions, workspace, raw_dir, processed_dir, status_changes)
    con.close()
    print("\nDone. Start the app; the page list should now show the original chapter numbers.")


if __name__ == "__main__":
    main()