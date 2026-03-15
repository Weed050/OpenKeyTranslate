from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime

class OcrText(BaseModel):
    text:str
    confidence: float
    box: List[List[float]]

class ProjectSchema(BaseModel):
    id: int
    name: str
    workspace_path: str
    created_at: datetime

    class Config:
        from_attributes = True

class UploadResponse(BaseModel):
    message: str
    id: Optional[int] = None
    filename: Optional[str] = ""
    extracted_texts: List[OcrText] = []