
# backend/services/page_export.py

"""
Shared page-export helper.

Extracted out of test_main.py so both the manual CLI entrypoint
(test_main.py) and the "process this page" API endpoint
(routers/pages.py) write the exact same JSON shape - the frontend editor
and the correction-memory system both depend on this format, so there must
be exactly one place that defines it.
"""

import json
import os
import tempfile

from core.config import SCALE


def save_ocr_json(out_dir: str, file_base_name: str, ocr_result: dict, pending_translation: bool = False) -> str:
    """
    Save OCR results (items, lines, bubbles with translations) to JSON.

    Used both for debugging/manual inspection and as the data source the
    frontend editor reads from (routers/pages.py GET /pages/{id}).

    Each bubble stores two translation fields:
    - ai_translation: the model's original output, written once here and
      never touched again - the baseline the editor's "Reset" button
      reverts to.
    - translation: the current value shown to the user. Starts equal to
      ai_translation, and is updated in place by update_bubble_translation()
      whenever the user saves an edit, so reopening the editor later shows
      their correction instead of reverting to the AI's first draft.

    :return: The full path the JSON was written to.
    """
    def orig(box):
        # OCR runs on an image scaled by SCALE; the editor + inpainting work on the ORIGINAL image.
        # (identity while ocr_scale == 1.0 - the previous "only lines up when scale == 1" caveat is gone)
        return [[round(x / SCALE, 1), round(y / SCALE, 1)] for x, y in box]

    # line -> owning bubble (bubble_id), for re-inpainting after a bubble is deleted / skipped (inpaint_service)
    owner = {lid: b["bubble_id"] for b in ocr_result["bubbles"] for lid in b.get("line_ids", [])}

    bubbles_out = []
    for b in ocr_result["bubbles"]:
        entry = {
            "bubble_id":      b["bubble_id"],
            "text":           b["text"],
            "translation":    b.get("translation", ""),
            "ai_translation": b.get("ai_translation", b.get("translation", "")),   # carried over by Re-OCR
            "box":            orig(b["box_coords"]),
            "line_count":     b["line_count"],
            "avg_score":      round(b["avg_score"], 4),
        }
        if b.get("text_raw"):
            entry["text_raw"] = b["text_raw"]          # raw OCR string when ocr_textfix changed the text
        if b.get("skip"):
            entry["skip"] = True                        # number / symbol-only bubble: listed, never translated/inpainted
            entry["skip_reason"] = b.get("skip_reason")
            entry["translation"] = entry["ai_translation"] = ""
        bubbles_out.append(entry)

    payload = {
        "items": [
            {
                "text":     item["text"],
                "score":    round(item["score"], 4),
                "slice_id": item.get("slice_id"),
                "box":      orig(item["box"]),
            }
            for item in ocr_result["items"]
        ],
        "bubbles": bubbles_out,
        "lines": [
            {"line_id": l["line_id"], "box": orig(l["box"]), "bubble_id": owner.get(l["line_id"])}
            for l in ocr_result.get("lines", [])
        ],
    }
    if pending_translation:
        payload["pending_translation"] = True   # prefetched: the editor translates when the page is opened

    out_path = os.path.join(out_dir, f"{file_base_name}_ocr.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print(f"[JSON] saved -> {out_path}")
    return out_path


def page_paths(page) -> dict:
    """
    Resolve the on-disk raw/JSON/inpainted-image paths for a Page ORM row.

    Single shared source of truth for this path layout - routers/pages.py
    and routers/corrections.py both import this rather than each computing
    it separately, so the two can never quietly drift apart.
    """
    chapter = page.chapter
    project = chapter.project
    file_base_name = os.path.splitext(page.file_name)[0]
    out_dir = os.path.join(project.workspace_path, "processed", chapter.number)
    return {
        "raw": os.path.join(project.workspace_path, "raw", chapter.number, page.file_name),
        "json": os.path.join(out_dir, f"{file_base_name}_ocr.json"),
        "image": os.path.join(out_dir, f"ocr_{file_base_name}_inpainted.png"),
        "out_dir": out_dir,
        "file_base_name": file_base_name,
    }


def load_page_json(json_path: str) -> dict:
    with open(json_path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_page_json(json_path: str, data: dict) -> None:
    """Atomic write (tmp file + os.replace): a crash mid-write can no longer leave a truncated page JSON."""
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(json_path), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, json_path)
    except Exception:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def update_bubble_translation(json_path: str, bubble_id: str, new_translation: str) -> bool:
    """
    Update a single bubble's current `translation` in a page's saved JSON,
    leaving `ai_translation` (the frozen original) untouched.

    Called whenever a correction is confirmed (routers/corrections.py), so
    the editor shows the user's saved edit next time the page loads instead
    of reverting to the AI's first draft every time.

    :return: True if the bubble was found and updated, False otherwise
        (missing file or unknown bubble_id).
    """
    if not os.path.exists(json_path):
        return False

    data = load_page_json(json_path)
    for b in data.get("bubbles", []):
        if b["bubble_id"] == bubble_id:
            b["translation"] = new_translation
            write_page_json(json_path, data)
            return True
    return False
