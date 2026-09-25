
# backend/models/models.py

"""
Database models schema definition using SQLAlchemy ORM.

Defines the entity relationships for Projects, Chapters, Pages, extracted
Text Blocks, and the correction-memory system (Corrections, TranslationLogs)
used by the OCR/Translation pipeline.
"""

from sqlalchemy import Column, Integer, String, Float, ForeignKey, DateTime, Text, LargeBinary
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

    # "active" | "archived". No archive/unarchive endpoint yet - this is the
    # schema placeholder for the sidebar's Active/Archived grouping; every
    # project defaults to "active" until that workflow is built.
    status = Column(String, default="active")

    # Relationships
    chapters = relationship("Chapter", back_populates="project", cascade="all, delete-orphan")
    corrections = relationship("Correction", back_populates="project", cascade="all, delete-orphan")

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


class Correction(Base):
    """
    A single confirmed user correction: the original English source text, the
    AI's initial (zero-shot) proposal, and the final Polish text the user
    approved.

    This is the core data structure behind the project's correction-memory
    feature (see services/memory_service.py). The embedding of `source_text`
    is used for similarity search, letting the translation service "remember"
    and reuse past corrections for text that reads as similar in the future.

    Corrections are scoped to a single project (not shared globally) to avoid
    one series' voice/phrasing bleeding into an unrelated one - see README
    "Known limitations" for the open question of whether this should ever be
    relaxed (e.g. across volumes of the same series).

    Rows are never overwritten in place: every confirmed bubble becomes a new
    row, so the memory grows across the project rather than collapsing
    similar-but-distinct lines into a single entry.
    """

    __tablename__ = "corrections"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False)

    source_text = Column(Text, nullable=False)
    ai_translation = Column(Text)
    final_translation = Column(Text, nullable=False)

    # Raw float32 embedding bytes for `source_text` (see services/memory_service.py
    # encode_embedding / decode_embedding). Stored as a plain column rather than in
    # a dedicated vector DB - at thesis scale, a brute-force in-memory cosine scan
    # over a few hundred/thousand rows is simpler to reason about and fast enough.
    embedding = Column(LargeBinary, nullable=False)

    reuse_count = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    # Relationships
    project = relationship("Project", back_populates="corrections")


class TranslationLog(Base):
    """
    Records every translation attempt made for a bubble, including both sides
    of the zero-shot vs. memory-injected comparison produced by
    services/translation_service.py.

    This is the raw data the thesis experiment is evaluated from: for bubbles
    where a correction-memory match was found, two rows are written (one per
    variant, sharing the same run_id) so the two outputs can later be
    compared - e.g. via edit distance or chrF - against the translation the
    user eventually approves for that bubble.
    """

    __tablename__ = "translation_logs"

    id = Column(Integer, primary_key=True, index=True)
    page_id = Column(Integer, ForeignKey("pages.id"), nullable=False)
    bubble_id = Column(String, nullable=False)

    variant = Column(String, nullable=False)  # "zero_shot" | "memory_injected"
    source_text = Column(Text, nullable=False)
    output_text = Column(Text)

    matched_correction_id = Column(Integer, ForeignKey("corrections.id"), nullable=True)
    similarity_score = Column(Float, nullable=True)
    threshold_used = Column(Float, nullable=True)  # MEMORY_SIMILARITY_THRESHOLD at the time of this match

    model_used = Column(String)
    key_label = Column(String, nullable=True)  # which key from the provider's pool served this call
    run_id = Column(String, nullable=False)  # groups the zero_shot / memory_injected pair from one translation pass

    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    # Relationships
    page = relationship("Page")
    matched_correction = relationship("Correction")
