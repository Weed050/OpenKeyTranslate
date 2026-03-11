from pydantic import BaseModel
from typing import List

class OcrText(BaseModel):
    text:str
    confidence: float
    box: List[List[float]]

class UploadResponse(BaseModel):
    filename: str
    message: str
    extracted_texts: List[OcrText] = []