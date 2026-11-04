# backend/routers/fonts.py

"""
Fonts for the lettering: the list the editor's font picker and Settings > Fonts show, and the user's own font files
(upload / delete) in <app root>/fonts. The logic lives in services/font_service.py; this router only translates HTTP.
Uploads come as JSON with base64 data, so no multipart parser is needed and the request goes through the same small
fetch helper as every other call of the frontend.
"""

import base64
import binascii
import os

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from services import font_service

router = APIRouter(prefix="/fonts", tags=["Fonts"])


@router.get("/")
def list_fonts(refresh: bool = False):
    """[{family, source: user | system, polish, styles: [regular, bold, italic, bold_italic]}] - Polish-capable first."""
    fams = font_service.list_families(refresh=refresh)
    out = [{"family": f["family"], "source": f["source"], "polish": f["polish"], "styles": sorted(f["styles"])} for f in fams]
    out.sort(key=lambda f: (f["source"] != "user", not f["polish"], f["family"].lower()))
    return {"fonts": out, "folder": font_service.FONTS_DIR}


@router.get("/user")
def list_user_fonts():
    """The files in the user font folder: [{file, family, style, polish}]."""
    return {"files": font_service.user_fonts(), "folder": font_service.FONTS_DIR}


@router.post("/user")
def upload_font(payload: dict):
    """{"name": "ComicNeue-Bold.ttf", "data": "<base64>"} -> the font is checked (Pillow opens it) and stored."""
    try:
        data = base64.b64decode(payload.get("data") or "", validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(status_code=400, detail="data must be base64")
    try:
        return font_service.save_user_font(payload.get("name") or "", data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/user/{file_name:path}/data")
def font_data(file_name: str):
    """The font file itself - Settings shows a sample line in each of the user's fonts (browser FontFace)."""
    folder = os.path.normpath(font_service.FONTS_DIR)
    target = os.path.normpath(os.path.join(folder, file_name))
    if not target.startswith(folder + os.sep) or not os.path.isfile(target):
        raise HTTPException(status_code=404, detail="Font file not found")
    return FileResponse(target, media_type="font/ttf" if target.lower().endswith(".ttf") else "application/octet-stream")


@router.delete("/user/{file_name:path}")
def delete_font(file_name: str):
    try:
        font_service.delete_user_font(file_name)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"deleted": file_name}
