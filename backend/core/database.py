
# backend/core/database.py

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from models.models import Base
from core.config import DATABASE_PATH

"""
Database configuration and session management module.

Sets up the SQLAlchemy engine, configures the session factory, 
and provides a dependency for database session lifecycles.
"""

# Format the path to ensure SQLite compatibility across different OS platforms
db_url_path = DATABASE_PATH.replace('\\', '/')
SQLALCHEMY_DATABASE_URL = f"sqlite:///./{db_url_path}"

# Initialize the database engine
# 'check_same_thread: False' is required for SQLite in multithreaded applications
engine = create_engine(
    SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread":False}
)

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