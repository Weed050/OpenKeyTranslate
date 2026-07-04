
# backend/models/models.py

"""
Database models schema definition using SQLAlchemy ORM.

Defines the entity relationships for Projects, Chapters, Pages,
and extracted Text Blocks within the OCR/Translation pipeline.
"""

from sqlalchemy import Column, Integer, String, Float, ForeignKey, DateTime, Text
from sqlalchemy.orm import relationship, declarative_base
from datetime import datetime, timezone

Base = declarative_base()

class Project(Base):
    """Represents a translation project containing multiple chapters."""

    __tablename__ = "projects"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, nullable=False)
    workspace_path = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    source_lang = Column(String, default="en")
    target_lang = Column(String, default="pl")

    # Relationships
    chapters = relationship("Chapter", back_populates="project", cascade="all, delete-orphan")

class Chapter(Base):
    """Represents a comic chapter (e.g., volume section or book chapter)."""

    __tablename__ = "chapters"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"))

    # Kept as String to accommodate non-integer identifiers (e.g., '1.5a', 'Prologue', 'Extra')
    number = Column(String, nullable=False)
    title = Column(String)
    raw_path = Column(String)
    processed_path = Column(String)

    # Relationships
    project = relationship("Project", back_populates="chapters")
    pages = relationship("Page", back_populates="chapter", cascade="all, delete-orphan")

class Page(Base):
    """Represents an individual page within a chapter."""

    __tablename__ = "pages"

    id = Column(Integer, primary_key=True, index=True)
    chapter_id = Column(Integer, ForeignKey("chapters.id"))
    file_name = Column(String, nullable=False)
    order = Column(Integer)
    status = Column(String, default="pending") # e.g., 'pending', 'processed', 'failed'

    # Relationships
    chapter = relationship("Chapter", back_populates="pages")
    blocks = relationship("TextBlock", back_populates="page", cascade="all, delete-orphan")

class TextBlock(Base):
    """Represents a single bounding box/polygon containing text detected via OCR."""

    __tablename__ = "text_blocks"

    id = Column(Integer, primary_key=True, index=True)
    page_id = Column(Integer, ForeignKey("pages.id"))

    # Serialized coordinate system for the bounding shape (e.g., JSON string of point coordinates)
    polygon_points = Column(Text, nullable=False)
    rotation_angle = Column(Float, default=0.0)

    # Text content caches
    text_en = Column(Text)
    text_pl = Column(Text)

    # Relationships
    page = relationship("Page", back_populates="blocks")