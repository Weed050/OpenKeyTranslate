from sqlalchemy import Column, Integer, String, Float, ForeignKey, DateTime, Text
from sqlalchemy.orm import relationship, declarative_base
from datetime import datetime, timezone

Base = declarative_base()

class Project(Base):
    __tablename__ = "projects"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, nullable=False)
    workspace_path = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    source_lang = Column(String, default="en")
    target_lang = Column(String, default="pl")

    chapters = relationship("Chapter", back_populates="project", cascade="all, delete-orphan")

class Chapter(Base):
    __tablename__ = "chapters"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"))
    number = Column(String, nullable=False) # Why string? Chapter 1.1, Chapter 1.5a, Extra, Prologue
    title = Column(String)

    project = relationship("Project", back_populates="chapters")
    pages = relationship("Page", back_populates="chapter", cascade="all, delete-orphan")

class Page(Base):
    __tablename__ = "pages"

    id = Column(Integer, primary_key=True, index=True)
    chapter_id = Column(Integer, ForeignKey("chapters.id"))
    file_name = Column(String, nullable=False)
    order = Column(Integer)
    status = Column(String, default="pending")

    chapter = relationship("Chapter", back_populates="pages")
    blocks = relationship("TextBlock", back_populates="page", cascade="all, delete-orphan")

class TextBlock(Base):
    __tablename__ = "text_blocks"

    id = Column(Integer, primary_key=True, index=True)
    page_id = Column(Integer, ForeignKey("pages.id"))

    polygon_points = Column(Text, nullable=False)

    rotation_angle = Column(Float, default=0.0)

    text_en = Column(Text)
    text_pl = Column(Text)

    page = relationship("Page", back_populates="blocks")