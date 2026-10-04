# backend/services/inpaint_service.py

"""
Re-inpainting from the saved page JSON.

The first inpainting pass (routers/pages.py) erases every text line that is not skipped. Since the JSON now keeps the
text LINES with the bubble that owns each one (page_export.save_ocr_json), the clean image can be rebuilt at any
time from the ORIGINAL file without re-running OCR:

  - delete a false-positive bubble  -> its lines are no longer erased (real artwork comes back exactly, no
    rectangle pasted over neighbours)
  - skip / un-skip a bubble          -> the original pixels return / the text is erased
  - lines with no owner (watermarks dropped by the ignore list) are always erased, like in the first pass.

Pages processed before this feature have no "lines" in their JSON -> reinpaint_from_json() returns False and the
caller falls back to pasting the original pixels back (routers/pages._restore_bubble_artwork) or asks for Re-OCR.
"""

from utils.image_io import load_image_bgr, write_image
from utils.inpainting import erase_text_from_image


def lines_to_erase(ocr_data: dict) -> list[dict] | None:
    """Lines that must be erased right now, or None when the JSON has no line data (old page)."""
    lines = ocr_data.get("lines")
    if lines is None:
        return None
    bubbles = {b["bubble_id"]: b for b in ocr_data.get("bubbles", [])}
    todo = []
    for line in lines:
        owner = line.get("bubble_id")
        if owner is None:
            todo.append(line)                                   # unassigned / watermark: always erased
        elif owner in bubbles and not bubbles[owner].get("skip"):
            todo.append(line)                                   # alive and translated: erased
    return todo


def reinpaint_from_json(paths: dict, ocr_data: dict) -> bool:
    """Rebuild paths["image"] from the raw file + saved lines. False when the JSON has no line data."""
    todo = lines_to_erase(ocr_data)
    if todo is None:
        return False
    image = load_image_bgr(paths["raw"])
    cleaned = erase_text_from_image(image, todo, lambda box: box)   # saved boxes are already original coordinates
    write_image(paths["image"], cleaned)
    return True
