
# backend/services/bubble_edit.py

"""
Manual bubble operations on a page's saved JSON (the editor's "Re-OCR bubble", "Add bubble", "Merge"):
pure functions on the ocr_data dict + image + an OCR function, so they are testable without a server.

JSON shape they work with (services/page_export.save_ocr_json):
  bubbles[]: {bubble_id, text, translation, ai_translation, box (4 points), line_count, avg_score,
              [text_raw, skip, skip_reason, interior_box, missed_text, refined]}
  lines[]:   {line_id, box, bubble_id}
All coordinates are original-image coordinates.
"""

from core.config import get_setting
from services.bubble_refine import bbox_of, center, expand, read_region, rect_poly, union_bbox
from utils.bubble_interior import find_interior
from utils.inpainting import analyze_bubble
from utils.ocr_textfix import fix_ocr_text, skip_reason


class BubbleEditError(Exception):
    """User-facing (message is shown as is); .status is the HTTP status the router should use."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _find(ocr_data: dict, bubble_id: str) -> dict:
    for b in ocr_data.get("bubbles", []):
        if b["bubble_id"] == bubble_id:
            return b
    raise BubbleEditError("Bubble not found on this page", 404)


def _next_number(ids, prefix: str) -> int:
    nums = [int(i.split("_")[-1]) for i in ids if i.startswith(prefix) and i.split("_")[-1].isdigit()]
    return max(nums, default=-1) + 1


def _replace_lines(ocr_data: dict, bubble_id: str, new_lines: list[dict]) -> None:
    if "lines" not in ocr_data:
        return       # page processed before line data existed: creating a partial list would make a rebuild bring the old text back
    lines = ocr_data["lines"]
    base = _next_number([l["line_id"] for l in lines], "line_")
    kept = [l for l in lines if l.get("bubble_id") != bubble_id]
    for k, l in enumerate(new_lines):
        kept.append({"line_id": f"line_{base + k}", "box": [[round(x, 1), round(y, 1)] for x, y in l["box"]], "bubble_id": bubble_id})
    ocr_data["lines"] = kept


def _target(ocr_data: dict, bubble: dict) -> dict:
    boxes = [l["box"] for l in ocr_data.get("lines", []) if l.get("bubble_id") == bubble["bubble_id"]]
    return {"bubble_id": bubble["bubble_id"], "bbox": bbox_of(bubble["box"]), "boxes": boxes}


def _refresh_info(image, ocr_data: dict, bubble: dict) -> None:
    if "lines" not in ocr_data:
        return       # no text boxes to compare against -> the analysis would flag every bubble
    _, info, _ = analyze_bubble(image, _target(ocr_data, bubble))
    bubble["interior_box"] = info["interior_box"]
    bubble["missed_text"] = bool(info["missed_text"])


def _clean(text: str, protected) -> str:
    return fix_ocr_text(text, protected) if get_setting("ocr_textfix", True) else text


def _reading_region(image, bubble: dict) -> tuple:
    """The whole bubble interior when it can be found, else the bubble box with some margin."""
    ih, iw = image.shape[:2]
    bbox = bbox_of(bubble["box"])
    it = find_interior(image, center(bbox), bbox)
    return expand(it.bbox if it else bbox, 0.02 if it else 0.2, iw, ih)


def reocr_bubble(ocr_data: dict, bubble_id: str, image, ocr_region_fn, protected=()) -> dict:
    """Read the bubble's whole region again as one crop and replace its text + lines. The old text goes to text_raw."""
    bubble = _find(ocr_data, bubble_id)
    reading = read_region(image, _reading_region(image, bubble), ocr_region_fn)
    if reading is None:
        raise BubbleEditError("No text was found in this bubble's area.", 422)

    new_text = _clean(reading["text"], protected)
    changed = new_text != bubble["text"]
    if changed:
        bubble.setdefault("text_raw", bubble["text"])
        bubble["text"] = new_text
    bubble.update(box=[[round(x, 1), round(y, 1)] for x, y in reading["box"]], line_count=len(reading["lines"]),
                  avg_score=round(reading["avg_score"], 4))
    bubble.pop("refined", None)
    _replace_lines(ocr_data, bubble_id, reading["lines"])
    reason = skip_reason(bubble["text"], skip_symbols=get_setting("ocr_skip_symbols", True))
    if reason and not (reason == "number" and not get_setting("ocr_skip_numeric", True)) and not bubble.get("skip"):
        bubble["skip"], bubble["skip_reason"] = True, reason
    _refresh_info(image, ocr_data, bubble)
    return {"bubble": bubble, "text_changed": changed}


def add_bubble(ocr_data: dict, box, image, ocr_region_fn, protected=()) -> dict:
    """OCR a rectangle the user drew and add its content as a NEW bubble (text OCR never saw)."""
    ih, iw = image.shape[:2]
    x1, y1, x2, y2 = (float(v) for v in box)
    x1, x2 = sorted((max(0.0, x1), min(float(iw), x2)))
    y1, y2 = sorted((max(0.0, y1), min(float(ih), y2)))
    if x2 - x1 < 12 or y2 - y1 < 12:
        raise BubbleEditError("The selected area is too small.", 400)
    reading = read_region(image, (x1, y1, x2, y2), ocr_region_fn)
    if reading is None:
        raise BubbleEditError("No text was found in the selected area.", 422)

    bubbles = ocr_data.setdefault("bubbles", [])
    bubble_id = f"bubble_{_next_number([b['bubble_id'] for b in bubbles], 'bubble_')}"
    text = _clean(reading["text"], protected)
    bubble = {
        "bubble_id": bubble_id, "text": text, "translation": "", "ai_translation": "",
        "box": [[round(x, 1), round(y, 1)] for x, y in reading["box"]], "line_count": len(reading["lines"]),
        "avg_score": round(reading["avg_score"], 4), "added_by_user": True,
    }
    if text != reading["text"]:
        bubble["text_raw"] = reading["text"]
    reason = skip_reason(text, skip_symbols=get_setting("ocr_skip_symbols", True))
    if reason and not (reason == "number" and not get_setting("ocr_skip_numeric", True)):
        bubble["skip"], bubble["skip_reason"] = True, reason

    cy = center(bbox_of(bubble["box"]))[1]
    index = next((i for i, b in enumerate(bubbles) if center(bbox_of(b["box"]))[1] > cy), len(bubbles))   # keep top-to-bottom order
    bubbles.insert(index, bubble)
    _replace_lines(ocr_data, bubble_id, reading["lines"])
    _refresh_info(image, ocr_data, bubble)
    return {"bubble": bubble, "index": index}


def merge_bubbles(ocr_data: dict, bubble_id: str, other_id: str, image=None) -> dict:
    """Merge `other_id` INTO `bubble_id` (a bubble the OCR cut into pieces). Text and translations are joined top to bottom."""
    if bubble_id == other_id:
        raise BubbleEditError("Choose two different bubbles.", 400)
    a, b = _find(ocr_data, bubble_id), _find(ocr_data, other_id)
    first, second = sorted((a, b), key=lambda x: (center(bbox_of(x["box"]))[1], center(bbox_of(x["box"]))[0]))

    join = lambda key: " ".join(v.strip() for v in (first.get(key, ""), second.get(key, "")) if v and v.strip())
    a_text_raw = join("text_raw") if (a.get("text_raw") or b.get("text_raw")) else None
    merged_text = join("text")
    a.update(text=merged_text, translation=join("translation"), ai_translation=join("ai_translation"),
             box=rect_poly(union_bbox(bbox_of(a["box"]), bbox_of(b["box"]))),
             line_count=a.get("line_count", 0) + b.get("line_count", 0))
    if a_text_raw:
        a["text_raw"] = a_text_raw
    a.pop("skip", None)
    a.pop("skip_reason", None)
    a["merged_by_user"] = True
    for line in ocr_data.get("lines", []):
        if line.get("bubble_id") == other_id:
            line["bubble_id"] = bubble_id
    ocr_data["bubbles"] = [x for x in ocr_data["bubbles"] if x["bubble_id"] != other_id]
    if image is not None:
        _refresh_info(image, ocr_data, a)
    return {"bubble": a, "removed": other_id}
