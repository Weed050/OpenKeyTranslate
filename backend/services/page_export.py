
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
from datetime import datetime, timezone

from core.config import SCALE, TEXT_THRESHOLD

OCR_DEBUG_MAX = 400          # entries per list in a page's ocr_debug (a junk-heavy page must not bloat the JSON)


def save_ocr_json(out_dir: str, file_base_name: str, ocr_result: dict, pending_translation: bool = False,
                  detect_rotation: bool | None = None, cleanups: list | None = None,
                  bubble_order: str | None = None, manual_ops: list | None = None) -> str:
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
        for flag in ("interior_box", "missed_text", "refined", "merged_from", "layout", "bulk_edits"):   # layout / bulk_edits: carried by Re-OCR
            if b.get(flag) is not None and b.get(flag) is not False:
                entry[flag] = b[flag]
        if b.get("confirmed_at") and not b.get("skip"):   # review stamp carried over by Re-OCR (utils.bubble_post.carry_over)
            entry["confirmed_translation"], entry["confirmed_at"] = b.get("confirmed_translation", ""), b["confirmed_at"]
            if b.get("confirmed_via"):
                entry["confirmed_via"] = b["confirmed_via"]   # "page": accepted with the whole page (review_page)
        if b.get("skip"):
            entry["skip"] = True                        # number / symbol-only bubble: listed, never translated/inpainted
            entry["skip_reason"] = b.get("skip_reason")
            entry["translation"] = entry["ai_translation"] = ""
        bubbles_out.append(entry)

    payload = {
        "items": [
            {
                "text":     item["text"],
                "score":    round(float(item["score"]), 4),
                "slice_id": item.get("slice_id"),
                "box":      orig(item["box"]),
                **({"rescued": item["rescued"]} if item.get("rescued") else {}),   # second-chance reading (utils/ocr_rescue)
            }
            for item in ocr_result["items"]
        ],
        "bubbles": bubbles_out,
        "lines": [   # text per line: lets the editor split a bubble without OCR (services/bubble_edit.split_bubble)
            {"line_id": l["line_id"], "box": orig(l["box"]), "bubble_id": owner.get(l["line_id"]), "text": l.get("text", ""),
             **({"angle": l["angle"]} if "angle" in l else {})}     # tilted line: slant (utils/merge_boxes.build_text_lines)
            for l in ocr_result.get("lines", [])
        ],
    }
    if pending_translation:
        payload["pending_translation"] = True   # prefetched: the editor translates when the page is opened
    if detect_rotation is not None:
        payload["detect_rotation"] = bool(detect_rotation)   # the page's own choice (Re-OCR dialog), see ocr_pipeline.PageOCR
    if cleanups:
        payload["cleanups"] = cleanups                       # manual "Clean area" rectangles (routers/pages.py add_cleanup)
    if bubble_order:
        payload["bubble_order"] = bubble_order               # reading order chosen in the editor (ttb / rtl / ltr)
    if manual_ops:
        payload["manual_ops"] = manual_ops                   # user's manual fixes so far, survive a Re-OCR (record_manual_op)
    dbg = ocr_result.get("debug") or {}
    if "rejected" in dbg:
        # what the OCR saw but did not use - the editor's OCR debug view (GET /pages/{id}/ocr-debug). Display only.
        payload["ocr_debug"] = {
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "threshold": TEXT_THRESHOLD,
            "slices": [[round(a / SCALE), round(b / SCALE)] for a, b in dbg.get("slices", [])],
            "rejected": [{"text": r["text"], "score": round(float(r["score"]), 4), "box": orig(r["box"])}
                         for r in dbg["rejected"][:OCR_DEBUG_MAX]],
            "dropped": [{"text": d["text"], "score": round(float(d.get("score") or 0), 4), "box": orig(d["box"]),
                         "why": d["why"]} for d in dbg.get("dropped", [])[:OCR_DEBUG_MAX]],
        }

    out_path = os.path.join(out_dir, f"{file_base_name}_ocr.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print(f"[JSON] saved -> {out_path}")
    return out_path


def bubble_reviewed(b: dict) -> bool:
    """The user looked at this bubble: edited it (translation != AI draft) or accepted it unchanged (confirmed stamp)."""
    text = (b.get("translation") or "").strip()
    if not text:
        return False
    return text != (b.get("ai_translation") or "").strip() or b.get("confirmed_translation") == b.get("translation")


_REVIEW_CACHE: dict[str, tuple[float, bool]] = {}


def page_reviewed(json_path: str) -> bool:
    """
    Every bubble to translate on the page has been reviewed by the user (same rule as the editor's list markers and the
    Lettering switch). A page without such bubbles (no text, only skipped numbers) counts as reviewed - nothing to check.
    Cached by file mtime: the project list asks for hundreds of pages.
    """
    try:
        mtime = os.path.getmtime(json_path)
    except OSError:
        return False
    hit = _REVIEW_CACHE.get(json_path)
    if hit and hit[0] == mtime:
        return hit[1]
    try:
        data = load_page_json(json_path)
        done = all(bubble_reviewed(b) for b in data.get("bubbles", []) if not b.get("skip"))
    except Exception:
        done = False
    _REVIEW_CACHE[json_path] = (mtime, done)
    return done


_STAGE_CACHE: dict[str, tuple[float, dict]] = {}


def page_stage(json_path: str) -> dict:
    """
    Where a PROCESSED page is in the work: {"stage", "bubbles", "translated", "reviewed", "page_accepted", "layout"} -
    bubbles = the ones to translate (not skipped; automatic skips such as page numbers do not count), translated = how
    many of them have a translation, reviewed = how many of them the user accepted or edited (page_accepted = how many
    of those only by "Accept page"), layout = the user marked the bubbles of the page as checked (review_page).
      "ocr"         read by the OCR, NO bubble translated yet (translation failed, or a prefetched page not opened yet)
      "partial"     some bubbles translated, some not (a failed batch, bubbles added / split / re-read later)
      "translated"  every bubble has a translation, not all are reviewed (AI draft / review in progress)
      "reviewed"    every bubble is reviewed (bubble_reviewed: edited, or accepted unchanged = moved on in the editor)
      "checked"     reviewed AND the bubbles (boxes, order, cleaning, lettering) marked as checked = fully reviewed
    A page with nothing to translate counts as text-reviewed. Cached by file mtime (the chapter list asks for every page).
    """
    empty = {"stage": "ocr", "bubbles": 0, "translated": 0, "reviewed": 0, "page_accepted": 0, "layout": False}
    try:
        mtime = os.path.getmtime(json_path)
    except OSError:
        return dict(empty)
    hit = _STAGE_CACHE.get(json_path)
    if hit and hit[0] == mtime:
        return hit[1]
    try:
        data = load_page_json(json_path)
        todo = [b for b in data.get("bubbles", []) if not b.get("skip")]
    except Exception:
        data, todo = None, None
    if todo is None:
        out = dict(empty)
    else:
        done = [b for b in todo if bubble_reviewed(b)]
        translated = sum(1 for b in todo if (b.get("translation") or "").strip())
        layout = bool(data.get("layout_checked_at"))
        if len(done) == len(todo):
            stage = "checked" if layout else "reviewed"
        elif translated == 0:
            stage = "ocr"
        elif translated < len(todo):
            stage = "partial"
        else:
            stage = "translated"
        out = {"stage": stage, "bubbles": len(todo), "translated": translated, "reviewed": len(done),
               "page_accepted": sum(1 for b in done if b.get("confirmed_via") == "page"), "layout": layout}
    _STAGE_CACHE[json_path] = (mtime, out)
    return out


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
        "project_dir": project.workspace_path,     # project-wide prefs (services/project_prefs.py)
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


MANUAL_OPS_MAX = 2000                 # events kept per page; past that only manual_ops_truncated is set
MANUAL_OPS_COALESCE = {"layout": 60, "layout_bulk": 60}   # op -> seconds: repeated calls count as one adjustment


def record_manual_op(data: dict, op: str, bubble: str | None = None, **detail) -> None:
    """
    Append one manual operation to the page JSON's `manual_ops` list: {"op", "at", "bubble"?, ...detail}.

    Why: the evaluation of "how much work the tool saves" counts what the user had to fix by hand per page - merges,
    splits, re-reads, source-text fixes, added / deleted bubbles, clean-ups, lettering adjustments, Re-OCR. Translation
    edits are NOT listed here (they are measured as edit distance between ai_translation and the final text), nor are
    review clicks (Accept page / Layout checked) or retranslations of a whole page (the editor runs those by itself for
    prefetched pages). Only the endpoints a user action reaches call this, so the list is a record of user actions.

    Lettering sends one request per drag or font change, so a "layout" call on the same bubble within 60 s of the last
    one only bumps that event's `n` and `last_at` (n = requests merged into one adjustment). "layout_bulk" = the
    requests of ONE click that styles many bubbles ("use on the whole page", dropping copies of a new project style):
    merged the same way while `how` stays the same. Detail values that are None are left out. Mutates `data`; the
    caller writes the JSON as usual.
    """
    now = datetime.now(timezone.utc)
    ops = data.setdefault("manual_ops", [])
    window = MANUAL_OPS_COALESCE.get(op)
    if window and ops:
        last = ops[-1]
        same_detail = all(last.get(k) == v for k, v in detail.items() if v is not None)
        if last.get("op") == op and last.get("bubble") == bubble and same_detail:
            try:
                age = (now - datetime.fromisoformat(last.get("last_at") or last["at"])).total_seconds()
            except (KeyError, TypeError, ValueError):
                age = None
            if age is not None and 0 <= age <= window:
                last["n"] = int(last.get("n", 1)) + 1
                last["last_at"] = now.isoformat(timespec="seconds")
                return
    if len(ops) >= MANUAL_OPS_MAX:
        data["manual_ops_truncated"] = True
        return
    event = {"op": op, "at": now.isoformat(timespec="seconds")}
    if bubble is not None:
        event["bubble"] = bubble
    event.update({k: v for k, v in detail.items() if v is not None})
    ops.append(event)


def update_bubble_translation(json_path: str, bubble_id: str, new_translation: str) -> bool:
    """
    Update a single bubble's current `translation` in a page's saved JSON,
    leaving `ai_translation` (the frozen original) untouched.

    Called whenever a correction is confirmed (routers/corrections.py), so
    the editor shows the user's saved edit next time the page loads instead
    of reverting to the AI's first draft every time.

    Also stamps the bubble as reviewed: `confirmed_at` + `confirmed_translation`. That is the only record of "the user
    read this and accepted it unchanged" now that unchanged translations no longer go into memory by default
    (memory_store_approved) - the evaluation needs it to tell an accepted line from one nobody looked at. The stamp is
    valid only while translation == confirmed_translation, so a later retranslation invalidates it by itself.

    :return: True if the bubble was found and updated, False otherwise
        (missing file or unknown bubble_id).
    """
    if not os.path.exists(json_path):
        return False

    data = load_page_json(json_path)
    for b in data.get("bubbles", []):
        if b["bubble_id"] == bubble_id:
            b["translation"] = new_translation
            b["confirmed_translation"] = new_translation
            b["confirmed_at"] = datetime.now(timezone.utc).isoformat()
            b.pop("confirmed_via", None)        # looked at on its own now, not only accepted with the whole page
            b.pop("split_check", None)          # the user looked at the divided translation of a split bubble
            write_page_json(json_path, data)
            return True
    return False


def review_page(json_path: str, layout: bool | None = None, accept: bool = False, unaccept: list | None = None) -> dict:
    """
    Page-level review (editor mode bar: "Accept page", "Layout checked", the "On next page" review mode).

    - layout True / False: set / clear `layout_checked_at` - the user checked the BUBBLES of the page (boxes, reading
      order, cleaning, lettering), not only their text. Kept in the page JSON; a Re-OCR writes a new JSON without it,
      because the bubbles are rebuilt.
    - accept: every bubble that has a translation and is not reviewed yet gets the review stamp of an unchanged
      translation (confirmed_translation / confirmed_at, as update_bubble_translation) PLUS confirmed_via = "page".
      Why the extra field: a bubble accepted with the whole page may never have been read on its own; the evaluation
      can tell such stamps apart (or leave them out) instead of counting them as a deliberate "accepted unchanged".
      No memory entry and no TranslationLog change - exactly like the silent per-bubble acceptance.
    - unaccept: bubble ids whose page-acceptance is undone (only stamps with confirmed_via == "page" are removed).

    :return: {"accepted": [bubble ids], "unaccepted": [...], "layout_checked_at": str | None}
    """
    data = load_page_json(json_path)
    now = datetime.now(timezone.utc).isoformat()
    accepted, unaccepted = [], []
    if accept:
        for b in data.get("bubbles", []):
            if b.get("skip") or not (b.get("translation") or "").strip() or bubble_reviewed(b):
                continue
            b["confirmed_translation"], b["confirmed_at"], b["confirmed_via"] = b["translation"], now, "page"
            b.pop("split_check", None)
            accepted.append(b["bubble_id"])
    if unaccept:
        wanted = set(unaccept)
        for b in data.get("bubbles", []):
            if b["bubble_id"] in wanted and b.get("confirmed_via") == "page":
                for key in ("confirmed_translation", "confirmed_at", "confirmed_via"):
                    b.pop(key, None)
                unaccepted.append(b["bubble_id"])
    if layout is True:
        data["layout_checked_at"] = now
    elif layout is False:
        data.pop("layout_checked_at", None)
    if accepted or unaccepted or layout is not None:
        write_page_json(json_path, data)
    return {"accepted": accepted, "unaccepted": unaccepted, "layout_checked_at": data.get("layout_checked_at")}
