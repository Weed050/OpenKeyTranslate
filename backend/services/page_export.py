
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


def save_ocr_json(out_dir: str, file_base_name: str, ocr_result: dict) -> str:
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
    payload = {
        "items": [
            {
                "text":     item["text"],
                "score":    round(item["score"], 4),
                "slice_id": item.get("slice_id"),
                "box":      [[round(x, 1), round(y, 1)] for x, y in item["box"]],
            }
            for item in ocr_result["items"]
        ],
        "bubbles": [
            {
                "bubble_id":      b["bubble_id"],
                "text":           b["text"],
                "translation":    b.get("translation", ""),
                "ai_translation": b.get("translation", ""),
                "box":            b["box_coords"],
                "line_count":     b["line_count"],
                "avg_score":      round(b["avg_score"], 4),
            }
            for b in ocr_result["bubbles"]
        ],
    }

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

    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    found = False
    for b in data.get("bubbles", []):
        if b["bubble_id"] == bubble_id:
            b["translation"] = new_translation
            found = True
            break

    if not found:
        return False

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return True
