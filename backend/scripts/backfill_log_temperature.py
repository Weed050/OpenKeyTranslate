
# backend/scripts/backfill_log_temperature.py

"""
Write the sampling temperature into translation logs made before it was logged (injection_json.temperature exists
since 2026-11-07, see services/translation_service.py).

Why: the thesis compares memory-injected vs zero-shot output; when the temperature changes, the logs have to be split
by it. Rows from before 2026-11-07 carry no value, but the user knows the setting they ran at (0.4 - the Settings
value on 2026-11-07, unchanged since logging started on 2026-10-28, per the user). This makes that knowledge explicit
in the data instead of in a footnote.

What changes (nothing else is ever touched): translation_logs.injection_json of rows WITHOUT a "temperature" key gets
    "temperature": <value>, "temperature_backfilled": "<date>"
The second key keeps the difference visible: a logged value vs. a value filled in afterwards from the user's
knowledge (History shows these with a "*"; drop them in an analysis if in doubt). Rows that already have a
temperature, and rows without injection_json, stay as they are.

Default is a DRY RUN. With --apply the DB is copied to <db>.temperature.<timestamp>.bak first (SQLite backup API,
safe while the backend runs), then all rows are updated in one transaction.

    python scripts/backfill_log_temperature.py --value 0.4            # what would change
    python scripts/backfill_log_temperature.py --value 0.4 --apply

The DB path defaults to %APPDATA%/OpenKeyTranslate/openkey_translate.db (the default app_root_dir); pass --db for a
moved data folder. settings.json is never read.
"""

import argparse
import json
import os
import sqlite3
from datetime import datetime


def default_db() -> str:
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(base, "OpenKeyTranslate", "openkey_translate.db")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--value", type=float, required=True, help="temperature the old logs ran at (e.g. 0.4)")
    ap.add_argument("--db", default=default_db(), help="path to openkey_translate.db")
    ap.add_argument("--apply", action="store_true", help="really write (default: dry run)")
    args = ap.parse_args()
    if not 0 <= args.value <= 2:
        raise SystemExit("--value must be between 0 and 2")
    if not os.path.isfile(args.db):
        raise SystemExit(f"No database at {args.db} (use --db)")

    con = sqlite3.connect(args.db, timeout=30)
    rows = con.execute("SELECT id, injection_json, created_at FROM translation_logs").fetchall()
    stamp = datetime.now().strftime("%Y-%m-%d")
    updates, has_value, no_json, broken = [], 0, 0, 0
    for log_id, text, _ in rows:
        if not text:
            no_json += 1
            continue
        try:
            data = json.loads(text)
        except ValueError:
            broken += 1
            continue
        if not isinstance(data, dict):
            broken += 1
            continue
        if "temperature" in data:
            has_value += 1
            continue
        data["temperature"] = args.value
        data["temperature_backfilled"] = stamp
        updates.append((json.dumps(data, ensure_ascii=False), log_id))

    dates = [r[2] for r in rows if r[1]]
    print(f"{len(rows)} log rows ({min(dates) if dates else '-'} .. {max(dates) if dates else '-'})")
    print(f"  to fill with temperature {args.value}: {len(updates)}")
    print(f"  already have a temperature: {has_value}; without injection_json: {no_json}; unreadable JSON: {broken}")
    if not args.apply:
        print("Dry run - nothing written. Add --apply to write.")
        return
    if not updates:
        print("Nothing to do.")
        return

    backup = f"{args.db}.temperature.{datetime.now():%Y%m%d_%H%M%S}.bak"
    with sqlite3.connect(backup) as dst:
        con.backup(dst)
    print(f"Backup: {backup}")
    with con:
        con.executemany("UPDATE translation_logs SET injection_json = ? WHERE id = ?", updates)
    print(f"Written: {len(updates)} rows.")


if __name__ == "__main__":
    main()
