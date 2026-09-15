
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

    Sent by the frontend editor whenever - both count as a correction for memory purposes, since even an unedited approval confirms the AI's proposal was already correct.
    """

    source_text: str
    ai_translation: Optional[str] = None
    final_translation: str