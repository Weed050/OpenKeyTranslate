
# backend/utils/bubble_post.py

"""
Post-OCR handling of bubbles that happens AFTER grouping and BEFORE translation / inpainting:

  1. text cleanup (utils/ocr_textfix.py): joins "SQUAD- RON", fixes LNIT -> UNIT. The raw OCR string is kept in
     bubble["text_raw"] (only when it differs) so nothing is ever lost and the editor can show both.
  2. skip flags: bubbles with no letters ("11", "9:55", "?!", "...") get bubble["skip"] = True +
     bubble["skip_reason"]. They stay in the editor list, are NOT sent to the LLM and their text lines are NOT
     inpainted (the original pixels stay on the page).
  3. carry_over(): used by Re-OCR to keep translations / manual edits of bubbles whose text did not change.

Both options are read LIVE from settings (ocr_textfix, ocr_skip_numeric, ocr_skip_symbols).
"""

import re

from core.config import get_setting
from utils.ocr_textfix import fix_ocr_text, skip_reason


def apply_bubble_postprocess(ocr_result: dict, protected=()) -> list[dict]:
    """
    Mutates ocr_result["bubbles"] (text / text_raw / skip / skip_reason) and returns the list of text LINES that
    should be inpainted (all lines except those of skipped bubbles). Also stored in ocr_result["inpaint_lines"].
    """
    textfix = get_setting("ocr_textfix", True)
    skip_numeric = get_setting("ocr_skip_numeric", True)
    skip_symbols = get_setting("ocr_skip_symbols", True)

    skipped_line_ids: set[str] = set()
    for b in ocr_result["bubbles"]:
        if textfix:
            fixed = fix_ocr_text(b["text"], protected)
            if fixed != b["text"]:
                b["text_raw"] = b["text"]
                b["text"] = fixed

        reason = skip_reason(b["text"], skip_symbols=skip_symbols)
        if reason == "number" and not skip_numeric:
            reason = None
        if reason:
            b["skip"] = True
            b["skip_reason"] = reason
            skipped_line_ids.update(b.get("line_ids", []))

    inpaint_lines = [l for l in ocr_result["lines"] if l["line_id"] not in skipped_line_ids]
    ocr_result["inpaint_lines"] = inpaint_lines
    return inpaint_lines


def _key(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", "", (text or "").lower()).split())


def carry_over(old_bubbles: list[dict], new_bubbles: list[dict]) -> set[str]:
    """
    Re-OCR: copy translation / ai_translation / manual skip / edited source text from OLD bubbles to NEW bubbles
    whose text is the same (case-, punctuation- and whitespace-insensitive; matched against old text AND text_raw).
    Each old bubble is used at most once. Returns the new bubble_ids that need no (re)translation.
    """
    pool = [o for o in old_bubbles if (o.get("translation") or "").strip() or o.get("skip_reason") == "manual"]
    carried: set[str] = set()
    for nb in new_bubbles:
        keys = {_key(nb["text"]), _key(nb.get("text_raw", ""))} - {""}
        for i, ob in enumerate(pool):
            if keys & {_key(ob.get("text", "")), _key(ob.get("text_raw", ""))}:
                if ob.get("text_raw"):                      # user had corrected the source text: keep their version
                    nb["text_raw"], nb["text"] = ob["text_raw"], ob["text"]
                if ob.get("skip_reason") == "manual":
                    nb["skip"], nb["skip_reason"] = True, "manual"
                if (ob.get("translation") or "").strip():
                    nb["translation"] = ob["translation"]
                    nb["ai_translation"] = ob.get("ai_translation", ob["translation"])
                carried.add(nb["bubble_id"])
                pool.pop(i)
                break
    return carried
