
# backend/models/schemas.py

"""
Pydantic (python lib name) schemas and Data Transfer Objects (DTOs).

Defines request/response validation structures and serialization
rules for the API endpoints.
"""

from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime

class OcrText(BaseModel):
    """Schema representing a single block of text extracted via OCR."""

    text:str
    confidence: float
    box: List[List[float]] # Coordinates representing the bounding box polygon

class ProjectSchema(BaseModel):
    """Schema for serialization and representation of Project entity data."""

    id: int
    name: str
    workspace_path: str
    created_at: datetime
    status: str = "active"

    class Config:
        from_attributes = True

class UploadResponse(BaseModel):
    """Standardized API response structure for file upload and processing endpoints."""

    message: str
    id: Optional[int] = None
    filename: Optional[str] = ""
    extracted_texts: List[OcrText] = []

class SettingsSchema(BaseModel):
    """Schema for validating configuration payloads and migration flags."""

    app_root_dir: str
    source_lang: str
    target_lang: str
    migrate_data: bool = False # Flag indicating whether to move files to the new root directory


class CorrectionSchema(BaseModel):
    """
    Payload for confirming a user-approved translation for a single bubble.

    Sent by the frontend editor when the user saves an edit, when they move on from an unchanged bubble (a plain
    "reviewed" confirmation, approve=False) and when they explicitly approve an unchanged translation into memory
    (approve=True). An edit always becomes a "corrected" memory entry; an unchanged translation becomes an "approved"
    entry ONLY with approve=True AND memory_store_approved on - moving on used to store AI text in memory silently.
    """

    source_text: str
    ai_translation: Optional[str] = None
    final_translation: str
    approve: bool = False


class CorrectionEditSchema(BaseModel):
    """Inline edit of a stored memory entry's final translation (Memory page)."""

    final_translation: str


class MemoryReplaceSchema(BaseModel):
    """
    Find & replace over a project's stored final translations (services/memory_service.replace_in_memory).
    mode "word" = whole words; "prefix" = words starting with `find` (the term + its inflected forms).
    apply=False returns a preview; ids limits the change to the entries left ticked in that preview.
    """

    find: str
    replace: str
    mode: str = "word"
    ids: Optional[List[int]] = None
    apply: bool = False
