
# backend/utils/image_io.py

"""
Robust page-image loader.

WHY: cv2.imdecode() returns None for ANY problem (corrupt file, wrong
extension, WebP/AVIF saved as .png, HTML error page saved by a scraper, ...).
The old code only said "Could not decode image" - no reason, no way out.

This loader:
  1. sniffs the real format from the first bytes (so the error says what the file REALLY is),
  2. tries OpenCV,
  3. falls back to Pillow (handles more formats, CMYK, 16-bit, truncated files),
  4. raises ImageLoadError with a human-readable reason otherwise.

ImageLoadError is NOT a ValueError on purpose: routers/pages.py maps ValueError -> 404,
and a broken file is not "not found" (use 422 for this error).
"""

import os

import cv2
import numpy as np
from PIL import Image, ImageFile

_SIGNATURES = [
    (b"\x89PNG\r\n\x1a\n", "PNG"),
    (b"\xff\xd8\xff", "JPEG"),
    (b"GIF8", "GIF"),
    (b"BM", "BMP"),
    (b"II*\x00", "TIFF"),
    (b"MM\x00*", "TIFF"),
]


class ImageLoadError(Exception):
    """Raised when an image file exists but cannot be decoded. str(e) is safe to show to the user."""


def sniff_format(head: bytes) -> str:
    """Best-effort real format from the first ~32 bytes of a file."""
    for sig, name in _SIGNATURES:
        if head.startswith(sig):
            return name
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "WEBP"
    if head[4:8] == b"ftyp":
        return "AVIF/HEIC"
    if head.lstrip()[:1] in (b"<", b"{"):
        return "TEXT (HTML/JSON error page, not an image)"
    return "UNKNOWN"


def load_image_bgr(path: str) -> np.ndarray:
    """
    Load an image as a BGR uint8 array (unicode-safe path handling).

    :raises FileNotFoundError: file does not exist.
    :raises ImageLoadError: file exists but is not a decodable image (message explains why).
    """
    if not os.path.exists(path):
        raise FileNotFoundError(path)

    size = os.path.getsize(path)
    name = os.path.basename(path)
    if size == 0:
        raise ImageLoadError(f"{name}: file is empty (0 bytes) - re-download / replace it.")

    with open(path, "rb") as f:
        head = f.read(32)
    detected = sniff_format(head)

    data = np.fromfile(path, np.uint8)
    try:
        img = cv2.imdecode(data, cv2.IMREAD_COLOR)
    except cv2.error:
        img = None
    if img is not None:
        return img

    # Pillow fallback (tolerates truncated files and exotic modes).
    old_flag = ImageFile.LOAD_TRUNCATED_IMAGES
    ImageFile.LOAD_TRUNCATED_IMAGES = True
    try:
        with Image.open(path) as im:
            rgb = np.array(im.convert("RGB"))
        print(f"[IMAGE_IO] {name}: OpenCV failed, Pillow fallback OK (detected={detected}).")
        return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    except Exception as e:
        raise ImageLoadError(
            f"{name}: cannot decode (detected format: {detected}; size: {size} B; "
            f"first bytes: {head[:12].hex(' ')}). Pillow: {type(e).__name__}: {e}. "
            f"Replace the file or exclude the page."
        ) from e
    finally:
        ImageFile.LOAD_TRUNCATED_IMAGES = old_flag


def write_image(path: str, image: np.ndarray) -> None:
    """
    Unicode-safe image writer. cv2.imwrite() silently returns False on Windows when the path has non-ASCII
    characters (user name / project name with Polish letters ...) - the page would be marked "processed" with
    NO image on disk. imencode + tofile works everywhere and raises on failure.
    """
    ext = os.path.splitext(path)[1] or ".png"
    ok, buf = cv2.imencode(ext, image)
    if not ok:
        raise ImageLoadError(f"Could not encode image for {path}")
    buf.tofile(path)