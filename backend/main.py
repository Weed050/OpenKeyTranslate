from typing import List
import os
from fastapi import FastAPI, Depends, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from routers import projects, settings
import uvicorn
from database import init_db
from paddleocr import PaddleOCR


@asynccontextmanager
async def lifespan(app: FastAPI):
    print("Database inicjalization ...")
    init_db()
    print("Database ready.")
    yield

    print("Closing session ...")

app = FastAPI(lifespan=lifespan)

# Konfiguracja cors - fontent:backend - middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # adres frontendu
    allow_methods = ["*"],
    allow_headers = ["*"],
)

app.include_router(projects.router)
app.include_router(settings.router)

ocr_model = PaddleOCR(use_textline_orientation=True, lang='en')

if __name__ == '__main__':
    uvicorn.run(app, host="127.0.0.1", port=8000)