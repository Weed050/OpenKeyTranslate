
# backend/services/inpaint_service.py

"""
Building the clean (text-erased) page image - one place for the first processing pass AND every later rebuild.

What is erased (utils/inpainting.build_erase_mask):
  - the text-line boxes of all bubbles that are not skipped (+ lines no bubble owns, e.g. watermarks the ignore list dropped)
  - inside verified white bubbles also dark pixels no OCR box explained (setting inpaint_use_interior)

The JSON keeps the text LINES with the bubble that owns each one (page_export.save_ocr_json), so the clean image can be
rebuilt at any time from the ORIGINAL file without OCR:
  - delete a false-positive bubble  -> its lines are no longer erased (real artwork comes back exactly)
  - skip / un-skip a bubble          -> the original pixels return / the text is erased
Pages processed before line data existed have no "lines" in their JSON -> reinpaint_from_json() returns False.
"""

from core.config import get_setting
from utils.image_io import load_image_bgr, write_image
from utils.inpainting import build_erase_mask, inpaint


def _bbox(box):
    xs, ys = [p[0] for p in box], [p[1] for p in box]
    return min(xs), min(ys), max(xs), max(ys)


def targets_from_result(ocr_result: dict, to_original_coords):
    """(boxes to erase, bubbles to analyse) from a fresh process_image() result; converted to original coordinates."""
    by_id = {l["line_id"]: l for l in ocr_result["lines"]}
    boxes = [to_original_coords(l["box"]) for l in ocr_result.get("inpaint_lines", ocr_result["lines"])]
    bubbles = []
    for b in ocr_result["bubbles"]:
        if b.get("skip"):
            continue
        bubbles.append({
            "bubble_id": b["bubble_id"], "bbox": _bbox(to_original_coords(b["box_coords"])),
            "boxes": [to_original_coords(by_id[i]["box"]) for i in b.get("line_ids", []) if i in by_id],
        })
    return boxes, bubbles


def targets_from_json(ocr_data: dict):
    """(boxes, bubbles) from a saved page JSON, or None when it has no line data (old page)."""
    lines = ocr_data.get("lines")
    if lines is None:
        return None
    alive = {b["bubble_id"]: b for b in ocr_data.get("bubbles", [])}
    boxes, owned = [], {}
    for line in lines:
        owner = line.get("bubble_id")
        if owner is None or (owner in alive and not alive[owner].get("skip")):
            boxes.append(line["box"])            # unowned (watermark) lines are always erased
            if owner is not None:
                owned.setdefault(owner, []).append(line["box"])
    bubbles = [{"bubble_id": bid, "bbox": _bbox(b["box"]), "boxes": owned.get(bid, [])}
               for bid, b in alive.items() if not b.get("skip")]
    return boxes, bubbles


def apply_info(bubble_dicts: list[dict], info: dict, key: str = "bubble_id") -> None:
    """Copy interior_box / missed_text from build_erase_mask's info into bubble dicts (result or JSON)."""
    for b in bubble_dicts:
        got = info.get(b[key])
        if got is not None:
            b["interior_box"] = got["interior_box"]
            b["missed_text"] = bool(got["missed_text"])


def reinpaint_from_json(paths: dict, ocr_data: dict) -> bool:
    """
    Rebuild paths["image"] from the raw file + saved lines. False when the JSON has no line data.
    Also refreshes interior_box / missed_text of the bubbles in `ocr_data` (the caller writes the JSON afterwards).
    """
    targets = targets_from_json(ocr_data)
    if targets is None:
        return False
    boxes, bubbles = targets
    image = load_image_bgr(paths["raw"])
    mask, info, flats = build_erase_mask(image, boxes, bubbles, use_interior=get_setting("inpaint_use_interior", True))
    apply_info(ocr_data.get("bubbles", []), info)
    write_image(paths["image"], inpaint(image, mask, flats))
    return True
