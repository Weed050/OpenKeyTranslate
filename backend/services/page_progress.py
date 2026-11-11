
# backend/services/page_progress.py

"""
Where every page of a project is in the work - one rule for the page list, the chapter bars and the project cards.

Why a module of its own: the stage of a page used to be computed twice (routers/pages.py for the page list,
routers/projects.py for the project card with only "processed / reviewed"), and the two drifted apart - the card
said "reviewed" for pages the list called "exported", and knew nothing of untranslated pages.

Stages, in pipeline order (a page is in exactly one):
  pending / queued / processing / failed   the DB status of a page the OCR has not finished (Page.status)
  ocr          read by the OCR, nothing translated yet
  partial      some bubbles translated, some not
  translated   every bubble translated, not all reviewed by the user (AI draft, review in progress)
  reviewed     every bubble's TEXT reviewed (accepted or edited) - ready to export
  checked      text reviewed AND the bubbles (boxes, reading order, cleaning, lettering) marked as checked in the
               editor (page JSON layout_checked_at, page_export.review_page) = fully reviewed
  exported     text reviewed (reviewed or checked) AND exported after its last change
"Corrected" is deliberately not a stage of its own: reviewing a bubble means accepting OR editing it, and which of the
two happened is per bubble (editor markers, correction memory), not per page.

Exports are remembered in <project folder>/export_log.json (page id -> time + destination), written by every export
(pages list, editor "Export page", any destination folder or ZIP). Before that log existed the only trace was a file
in <project>/exported/<chapter>/, which is still accepted - so exports to another folder count too, and old exports are
not forgotten. The log is display data only: it never changes what is translated or logged for the experiment.
"""

import json
import os
import threading
import time

from services.page_export import page_paths, page_stage, write_page_json

EXPORT_LOG_NAME = "export_log.json"
_LOG_LOCK = threading.Lock()

# stages counted as "work done by the OCR" (the page has a JSON); the rest come straight from Page.status
DONE_STAGES = ("ocr", "partial", "translated", "reviewed", "checked", "exported")
REVIEWED_STAGES = ("reviewed", "checked", "exported")          # the text of every bubble was looked at


def export_log_path(project) -> str:
    return os.path.join(project.workspace_path, EXPORT_LOG_NAME)


def load_export_log(project) -> dict:
    """{str(page_id): {"at": epoch seconds, "dest": folder or zip}}; {} when nothing was exported yet."""
    try:
        with open(export_log_path(project), "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def record_exports(project, page_ids, dest: str) -> None:
    """Stamp the given pages as exported now (to `dest`). Never raises: a failed stamp must not fail the export."""
    if not page_ids:
        return
    now = time.time()
    try:
        with _LOG_LOCK:
            log = load_export_log(project)
            for pid in page_ids:
                log[str(pid)] = {"at": now, "dest": dest}
            os.makedirs(project.workspace_path, exist_ok=True)
            write_page_json(export_log_path(project), log)      # atomic tmp + replace, same as the page JSONs
    except Exception as e:                                       # noqa: BLE001 - display data only
        print(f"[EXPORT] couldn't update {EXPORT_LOG_NAME}: {e}")


def _export_still_there(entry: dict, chapter, stem: str) -> bool:
    """
    Is the export a log entry points at still on disk? "dest" is the ZIP, the chapter folder ("Export page") or the
    folder holding the chapter folders (Export pages). A dest that is not a folder or a ZIP (an entry without one) is
    trusted. Since 2026-11-11 - before, an export deleted by hand still counted as exported.
    """
    dest = entry.get("dest")
    if not isinstance(dest, str) or not dest:
        return True
    if dest.lower().endswith(".zip"):
        return os.path.isfile(dest)
    if not os.path.isdir(dest):
        return False
    return any(os.path.exists(os.path.join(folder, f"{stem}.{ext}"))
               for folder in (dest, os.path.join(dest, chapter.number)) for ext in ("png", "jpg"))


def _exported_at(page, chapter, project, export_log: dict) -> float | None:
    """
    Last export time of a page: the export log (while its file is still there, _export_still_there), else an image
    in <project>/exported/<chapter>/ (old exports).
    """
    entry = export_log.get(str(page.id))
    stem = os.path.splitext(page.file_name)[0]
    times = ([entry["at"]] if isinstance(entry, dict) and isinstance(entry.get("at"), (int, float))
             and _export_still_there(entry, chapter, stem) else [])
    folder = os.path.join(project.workspace_path, "exported", chapter.number)
    for ext in ("png", "jpg"):
        f = os.path.join(folder, f"{stem}.{ext}")
        if os.path.exists(f):
            try:
                times.append(os.path.getmtime(f))
            except OSError:
                pass
    return max(times) if times else None


def page_progress(page, chapter, project, export_log: dict) -> dict:
    """
    {"stage", "bubbles", "translated", "reviewed", "export", "updated"} of one page:
    export = "current" | "outdated" (changed after the last export) | absent (never exported);
    updated = mtime of the page JSON (last OCR / edit / Lettering change), for "sort by last activity".
    """
    if page.status != "processed":
        return {"stage": page.status}
    paths = page_paths(page)
    info = dict(page_stage(paths["json"]))
    try:
        info["updated"] = os.path.getmtime(paths["json"])
    except OSError:
        info["updated"] = None
    exported = _exported_at(page, chapter, project, export_log)
    if exported is not None:
        info["export"] = "current" if info["updated"] is None or exported >= info["updated"] else "outdated"
        info["exported_at"] = exported
        if info["stage"] in ("reviewed", "checked") and info["export"] == "current":
            info["stage"] = "exported"
    return info


def project_progress(project) -> dict:
    """
    Stage counts of the project's pages (excluded ones left out) + the last activity:
    {"total", "stages": {stage: n}, "processed", "reviewed" (text reviewed: reviewed + checked + exported),
    "last_activity"}. "processed" / "reviewed" keep the old fields of GET /projects/{id}/progress.
    Extra counts in "stages", not stages of their own: exported_checked (exported + fully reviewed) and
    exported_draft (exported before the review, still in its stage - since 2026-11-11).
    """
    export_log = load_export_log(project)
    stages: dict[str, int] = {}
    total = 0
    last = None
    for chapter in project.chapters:
        for page in chapter.pages:
            if page.status == "excluded":
                continue
            total += 1
            info = page_progress(page, chapter, project, export_log)
            stages[info["stage"]] = stages.get(info["stage"], 0) + 1
            if info["stage"] == "exported" and info.get("layout"):
                # exported pages that were fully reviewed: the cards fold "exported" back into the review stage
                stages["exported_checked"] = stages.get("exported_checked", 0) + 1
            elif info.get("export") == "current" and info["stage"] not in REVIEWED_STAGES:
                # exported before the review (AI text as it was): stays in its stage, counted for the card's note
                stages["exported_draft"] = stages.get("exported_draft", 0) + 1
            if info.get("updated") and (last is None or info["updated"] > last):
                last = info["updated"]
    return {
        "total": total,
        "stages": stages,
        "processed": sum(stages.get(s, 0) for s in DONE_STAGES),
        "reviewed": sum(stages.get(s, 0) for s in REVIEWED_STAGES),
        "last_activity": last,
    }
