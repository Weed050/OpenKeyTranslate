from fastapi import FastAPI, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from schemas import UploadResponse
import uvicorn

app = FastAPI()

# Konfiguracja cors - fontent:backend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # adres frontendu
    allow_methods = ["*"],
    allow_headers = ["*"],
)

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