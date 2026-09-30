
# backend/scripts/fix_chapter_order.py

"""
One-off fix for project "The Galactic Navy Officer Becomes an Adventurer":
import_folder() used a plain alphabetical dirs.sort() instead of
natural_sort_key(), so once the source folder count hit double digits
(chapter_10, chapter_11...) the OS-walk order got scrambled - "chapter_10"
sorts before "chapter_2" alphabetically. Chapters got imported into the
wrong Chapter_N slot.

This renames the raw/ and processed/ folders back to their correct number
and updates the matching DB rows (number, title, raw_path, processed_path)
so everything lines up again. Chapter_0 and Chapter_1 are untouched (they
were coincidentally imported correctly - single digits sort fine).

*** BACK UP THE WHOLE PROJECT FOLDER AND THE DATABASE FILE BEFORE RUNNING. ***
Close the app first (file locks / in-use DB). One-shot script - if it fails
partway through, restore from backup before re-running, don't re-run blind.

Usage:
    cd backend
    python scripts/fix_chapter_order.py
"""

import os
import sys
import sqlite3

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.config import DATABASE_PATH

PROJECT_NAME = "The Galactic Navy Officer Becomes an Adventurer"

# app_number -> true_number (derived from the alphabetical-sort bug)
REMAP = {
    2: 10, 3: 11, 4: 12, 5: 13,
    6: 2,  7: 3,  8: 4,  9: 5,
    10: 6, 11: 7, 12: 8, 13: 9,
}


def main():
    if not os.path.exists(DATABASE_PATH):
        print(f"Database not found at {DATABASE_PATH} - abort.")
        return

    con = sqlite3.connect(DATABASE_PATH)
    cur = con.cursor()

    cur.execute("SELECT id, workspace_path FROM projects WHERE name=?", (PROJECT_NAME,))
    row = cur.fetchone()
    if not row:
        print(f"Project '{PROJECT_NAME}' not found - abort.")
        con.close()
        return

    project_id, workspace_path = row
    raw_dir = os.path.join(workspace_path, "raw")
    processed_dir = os.path.join(workspace_path, "processed")

    if not os.path.isdir(raw_dir):
        print(f"Raw dir not found at {raw_dir} - abort. Check workspace_path.")
        con.close()
        return

    print(f"Project id={project_id}, workspace={workspace_path}")
    print("Renaming folders (pass 1: app number -> temp name)...")

    # Pass 1: move every affected chapter folder to a temp name first,
    # so the cyclic remap (2->10->6->2, etc.) never overwrites a folder
    # that hasn't been read yet.
    for app_n in REMAP:
        for base in (raw_dir, processed_dir):
            src = os.path.join(base, f"Chapter_{app_n}")
            tmp = os.path.join(base, f"Chapter_{app_n}__tmp")
            if os.path.isdir(src):
                os.rename(src, tmp)
                print(f"  {src} -> {tmp}")
            else:
                print(f"  [SKIP] {src} does not exist")

    print("Renaming folders (pass 2: temp name -> true number)...")

    # Pass 2: move temp names to their true chapter number.
    for app_n, true_n in REMAP.items():
        for base in (raw_dir, processed_dir):
            tmp = os.path.join(base, f"Chapter_{app_n}__tmp")
            dst = os.path.join(base, f"Chapter_{true_n}")
            if os.path.isdir(tmp):
                os.rename(tmp, dst)
                print(f"  {tmp} -> {dst}")

    print("Updating database rows...")

    for app_n, true_n in REMAP.items():
        old_number = f"Chapter_{app_n}"
        new_number = f"Chapter_{true_n}"
        new_title = f"Chapter {true_n}"
        new_raw_path = os.path.join(raw_dir, new_number)
        new_processed_path = os.path.join(processed_dir, new_number)

        cur.execute(
            """UPDATE chapters SET number=?, title=?, raw_path=?, processed_path=?
               WHERE project_id=? AND number=?""",
            (new_number, new_title, new_raw_path, new_processed_path,
             project_id, old_number),
        )
        print(f"  DB: {old_number} -> {new_number} ({cur.rowcount} row updated)")

    con.commit()
    con.close()
    print("\nDone. Start the app and check the Pages list - order should read correctly now.")
    print("If something looks wrong, restore raw/, processed/ and the DB from your backup.")


if __name__ == "__main__":
    main()