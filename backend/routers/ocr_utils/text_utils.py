import re

def _normalize_ocr_text(text: str) -> str:
    """Strip noise from OCR text, keeping only alphanumeric characters."""
    t = text.strip()
    t = re.sub(r"[\s]+", "", t)
    t = re.sub(r"[^a-zA-Z0-9]", "", t)
    return t

def is_any_marker(text: str) -> bool:
    """Sprawdza, czy tekst zawiera dowolny marker."""
    normalized = _normalize_ocr_text(text)
    return bool(re.search(r"M\d+", normalized, re.IGNORECASE))