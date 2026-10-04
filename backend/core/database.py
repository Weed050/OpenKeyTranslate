
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

def init_db():
    """Create all defined database tables if they do not already exist."""
    Base.metadata.create_all(bind=engine)

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