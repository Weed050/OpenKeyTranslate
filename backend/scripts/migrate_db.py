
# backend/scripts/migrate_db.py
"""
One-off manual migration script for schema changes that
Base.metadata.create_all() can't apply to an existing SQLite file (it only
creates missing tables, never adds columns to existing ones).

Run this BEFORE starting the app after pulling a change that adds a new
column to an existing model. Safe to re-run - each migration checks
whether its column already exists before touching anything.

Usage:
    cd backend
    python scripts/migrate_db.py
"""
import sqlite3
import sys
import os
import shutil

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.config import DATABASE_PATH


def _column_exists(cur, table, column):
    cur.execute(f"PRAGMA table_info({table})")
    return any(row[1] == column for row in cur.fetchall())


def _add_column_if_missing(cur, table, column, coltype):
    if _column_exists(cur, table, column):
        print(f"[MIGRATE] {table}.{column} already exists - skipping.")
        return
    cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")
    print(f"[MIGRATE] Added {table}.{column} ({coltype}).")


def main():
    if not os.path.exists(DATABASE_PATH):
        print(f"[MIGRATE] No database found at {DATABASE_PATH} - nothing to migrate.")
        return

    backup_path = DATABASE_PATH + ".bak"
    if not os.path.exists(backup_path):
        shutil.copy2(DATABASE_PATH, backup_path)
        print(f"[MIGRATE] Backed up database to {backup_path}")
    else:
        print(f"[MIGRATE] Backup already exists at {backup_path} - not overwriting.")

    conn = sqlite3.connect(DATABASE_PATH)
    cur = conn.cursor()

    # --- migrations list: add new ones here as the schema grows ---
    _add_column_if_missing(cur, "translation_logs", "threshold_used", "FLOAT")

    conn.commit()
    conn.close()
    print("[MIGRATE] Done.")


if __name__ == "__main__":
    main()