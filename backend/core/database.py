
# backend/core/database.py

from sqlalchemy import create_engine, event
from sqlalchemy.engine import URL
from sqlalchemy.orm import sessionmaker
from models.models import Base
from core.config import DATABASE_PATH

"""
Database configuration and session management module.

Sets up the SQLAlchemy engine, configures the session factory, 
and provides a dependency for database session lifecycles.
"""

# URL.create() builds a correct sqlite URL for any absolute path. The old f"sqlite:///./{path}" only worked on
# Windows by accident (ntpath.normpath drops the "./"); on Linux/macOS it became a RELATIVE path under the current
# directory ("unable to open database file") - reproduced by the test-suite.
SQLALCHEMY_DATABASE_URL = URL.create("sqlite", database=DATABASE_PATH)

# Initialize the database engine
# 'check_same_thread: False' is required for SQLite in multithreaded applications
engine = create_engine(
    SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False, "timeout": 30}
)


@event.listens_for(engine, "connect")
def _sqlite_pragmas(dbapi_connection, _record):
    """WAL + busy timeout: the background OCR worker and request threads write concurrently ("database is locked")."""
    cur = dbapi_connection.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA busy_timeout=30000")
    cur.close()

# Session factory for generating clean database sessions
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def migrate_schema() -> list[str]:
    """
    Additive migration: add every model column that is missing from an EXISTING database table.

    Base.metadata.create_all() creates missing TABLES but never adds columns to existing ones, so every release that
    added a column needed a manual scripts/migrate_db.py run - and an old database (or a backup restored from an
    older version) failed at the first INSERT with "no such column". Runs on every start and after an import.
    Only nullable / defaulted columns can be added in place; anything else is reported, never guessed.
    """
    from sqlalchemy import inspect, text
    added = []
    insp = inspect(engine)
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if not insp.has_table(table.name):
                continue
            existing = {c["name"] for c in insp.get_columns(table.name)}
            for column in table.columns:
                if column.name in existing:
                    continue
                if not column.nullable and column.default is None and column.server_default is None:
                    print(f"[MIGRATE] WARNING {table.name}.{column.name} is NOT NULL without default - cannot add automatically")
                    continue
                coltype = column.type.compile(engine.dialect)
                conn.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {coltype}'))
                added.append(f"{table.name}.{column.name}")
                print(f"[MIGRATE] added column {table.name}.{column.name} ({coltype})")
    return added


def init_db():
    """Create missing tables, then add missing columns to existing ones."""
    Base.metadata.create_all(bind=engine)
    migrate_schema()

def get_db():
    """
    Context manager / Dependency generator for database sessions.

    Yields a session instance and guarantees its closure after use.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()