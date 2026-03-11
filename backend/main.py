from fastapi import FastAPI, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from schemas import UploadResponse, OcrText
import uvicorn
import numpy as np
import cv2
from paddleocr import PaddleOCR

app = FastAPI()

# Konfiguracja cors - fontent:backend - middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # adres frontendu
    allow_methods = ["*"],
    allow_headers = ["*"],
)

ocr_model = PaddleOCR(use_angle_cls=True, lang='en')

@app.get("/")
def read_root():
    return {"status":"OpenKeyTranslate API is running"}

@app.post("/upload-page", response_model=UploadResponse)
async def upload_page(file: UploadFile = File(...)):
    return UploadResponse(
        filename = file.filename,
        message = "Image recieved. Ready for OCR to process it.")

if __name__ == '__main__':
    uvicorn.run(app, host="127.0.0.1", port=8000)