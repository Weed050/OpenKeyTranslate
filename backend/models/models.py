
# backend/models/models.py

"""
Database models schema definition using SQLAlchemy ORM.

Defines the entity relationships for Projects, Chapters, Pages, extracted
Text Blocks, and the correction-memory system (Corrections, TranslationLogs)
used by the OCR/Translation pipeline.
"""

import os
import re

from sqlalchemy import Column, Integer, String, Float, ForeignKey, DateTime, Text, LargeBinary, Boolean
from sqlalchemy.orm import relationship, declarative_base
from datetime import datetime, timezone

from core import config

Base = declarative_base()

class Project(Base):
    """Represents a translation project containing multiple chapters."""

    __tablename__ = "projects"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, nullable=False)

    # PORTABILITY: the real folder is ALWAYS <workspace root from settings.json>/<project name> (see the
    # workspace_path property). The DB column only keeps the folder NAME - an absolute path stored here broke every
    # move to another PC / drive (startup treated the moved project as new and crashed on UNIQUE(name), or deleted it).
    _stored_workspace = Column("workspace_path", String, nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    source_lang = Column(String, default="en")
    target_lang = Column(String, default="pl")

    # "active" | "archived". No archive/unarchive endpoint yet - this is the
    # schema placeholder for the sidebar's Active/Archived grouping; every
    # project defaults to "active" until that workflow is built.
    status = Column(String, default="active")

    @property
    def workspace_path(self) -> str:
        """Absolute project folder on THIS machine, derived from the current workspace root + project name."""
        return os.path.join(config.WORKSPACE_DIR, self.name)

    @workspace_path.setter
    def workspace_path(self, value) -> None:
        # Accepted so old call sites (Project(name=..., workspace_path=...)) keep working; only the folder name is kept.
        # split on BOTH separators: a path written by a Windows machine must reduce to its folder name on Linux/macOS too
        self._stored_workspace = re.split(r"[\\/]", str(value).rstrip("\\/"))[-1] if value else self.name

    # Relationships
    chapters = relationship("Chapter", back_populates="project", cascade="all, delete-orphan")
    corrections = relationship("Correction", back_populates="project", cascade="all, delete-orphan")
    glossary_terms = relationship("GlossaryTerm", back_populates="project", cascade="all, delete-orphan")

class Chapter(Base):
    """Represents a comic chapter (e.g., volume section or book chapter)."""

    __tablename__ = "chapters"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"))

    # Kept as String to accommodate non-integer identifiers (e.g., '1.5a', 'Prologue', 'Extra')
    number = Column(String, nullable=False)
    title = Column(String)
    # Stored RELATIVE to the project folder ("raw/Chapter_2"); the properties below build the absolute path.
    _stored_raw = Column("raw_path", String)
    _stored_processed = Column("processed_path", String)

    @property
    def raw_path(self) -> str:
        return os.path.join(self.project.workspace_path, "raw", self.number)

    @raw_path.setter
    def raw_path(self, value) -> None:
        self._stored_raw = f"raw/{self.number}" if self.number else None

    @property
    def processed_path(self) -> str:
        return os.path.join(self.project.workspace_path, "processed", self.number)

    @processed_path.setter
    def processed_path(self, value) -> None:
        self._stored_processed = f"processed/{self.number}" if self.number else None

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

    # "corrected": the user changed the AI proposal (strongest signal, ranked first at retrieval).
    # "approved":  the user accepted the AI proposal unchanged - still a confirmed EN->PL pair, i.e. a regular
    #              translation-memory entry (Moslem 2024 retrieves from the whole TM, not only from edits).
    # NULL = rows from before this column existed = "corrected" (the old code stored edits only).
    kind = Column(String, nullable=True, default="corrected")

    # Edited later on the Memory page (inline edit / find & replace), e.g. a term the user changed their mind about
    # (WIWERN -> WYWERN). final_before_edit keeps the text as it was first saved from the editor, so the evaluation
    # can tell an in-editor correction from a later memory clean-up. NULL = never edited.
    edited_at = Column(DateTime(timezone=True), nullable=True)
    final_before_edit = Column(Text, nullable=True)

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
    glossary_terms_used = Column(Text,
                                 nullable=True)  # JSON list of {"source_term":..., "target_term":...} that fired for this bubble
    # The EXACT memory material sent with this bubble: {"examples": [{en, pl, similarity, kind, rejected?}, ...]}
    # (fuzzy lane, best first) or {"exact": "..."}. Logs written before the EN->PL pairs hold {"hints": [pl, ...]}.
    # matched_correction_id only points at the top-1 correction, which may later be deleted or superseded - this keeps
    # the experiment reproducible.
    hints_json = Column(Text, nullable=True)
    # Which memory lane produced the injected material: "exact" (short phrase, verbatim reuse) | "fuzzy" (embedding
    # examples) | NULL (no memory). Lets the analysis separate trivial exact hits ("YES" -> "TAK") from fuzzy matches.
    memory_lane = Column(String, nullable=True)
    # EVERYTHING sent with / done to this bubble, as one JSON object (see translation_service._injection_record):
    # memory examples (en/pl/rejected/similarity/kind/correction_id), glossary terms, context bubbles, the terminology
    # check + post-edit (draft before the fix, which terms were missing), case normalization. For the experiment
    # write-up: "what exactly did the model see, and what happened afterwards".
    injection_json = Column(Text, nullable=True)
    run_id = Column(String, nullable=False)  # groups the zero_shot / memory_injected pair from one translation pass

    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    # Relationships
    page = relationship("Page")
    matched_correction = relationship("Correction")

class GlossaryTerm(Base):
    """
    Deterministic term-base (English -> Polish), separate from the fuzzy
    embedding-based Correction memory. Correction suggests STYLE for a
    similar whole segment; GlossaryTerm forces a FIXED translation for one
    word/phrase, regardless of what sentence it shows up in.

    Matched by word presence (word-boundary regex), not similarity -
    catches recurring proper nouns/invented terms that will never clear a
    whole-bubble similarity threshold.
    """
    __tablename__ = "glossary_terms"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False)

    source_term = Column(String, nullable=False)
    target_term = Column(String, nullable=False)

    auto_detected = Column(Boolean, default=False)
    occurrences = Column(Integer, default=1)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    project = relationship("Project", back_populates="glossary_terms")
