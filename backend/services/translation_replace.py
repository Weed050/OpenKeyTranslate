
# backend/services/translation_replace.py

"""
Find & replace in the TRANSLATIONS of a whole project (the bubbles' current text in the page JSONs) - e.g. a term the
user changed their mind about ("ŻELAZNY SMOK" -> "STALOWY SMOK") after it was already accepted on many pages.
Before, every bubble had to be found and edited by hand; the Memory page could only fix the stored corrections.

Matching is the memory's find & replace (services/memory_service.replacer): whole words / phrases, or a word with its
Polish inflected forms ("prefix"), the replacement keeps the letter case of what it replaces.

What a replacement does to a bubble (and why):
  - translation      the new text. ai_translation (the AI draft, the experiment's baseline) is never touched, so a
                     replaced bubble counts as "edited" from then on - the user did change the AI text.
  - confirmed_translation  follows the new text when the bubble was reviewed with the old one (it stays reviewed);
                     a bubble nobody reviewed yet gets no review stamp (it still shows the edited marker, see the
                     dialog's warning).
  - bulk_edits       [{"at", "find", "replace"}] appended - since 2026-11-05: lets the evaluation tell a bulk
                     replacement from an edit made in the editor (both change translation vs ai_translation).
The correction memory is NOT changed here (a bulk replacement is not a correction of one line); the dialog offers the
memory's own find & replace for that.
"""

import os
from datetime import datetime, timezone

from services.memory_service import replacer
from services.page_export import load_page_json, page_paths, write_page_json


def _pages_in_order(project):
    from utils.chapter_labels import chapter_sort_key
    for chapter in sorted(project.chapters, key=lambda c: chapter_sort_key(c.number)):
        for page in sorted(chapter.pages, key=lambda p: p.order or 0):
            if page.status == "processed":
                yield chapter, page


def replace_in_translations(project, find: str, replace: str, mode: str = "word", keys: list[str] | None = None,
                            apply: bool = False) -> list[dict]:
    """
    Every bubble of the project whose translation changes: [{"key": "page_id:bubble_id", "page_id", "bubble_id",
    "chapter", "file_name", "bubble_no", "source", "before", "after", "reviewed"}] in reading order of the project.
    apply=False = preview; apply=True changes only `keys` when given (the rows left ticked in the dialog).
    """
    from services.page_export import bubble_reviewed
    find = (find or "").strip()
    if len(find) < 2:
        return []
    pattern, substitute = replacer(find, replace, mode)
    wanted = set(keys) if keys is not None else None
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    changes = []
    for chapter, page in _pages_in_order(project):
        path = page_paths(page)["json"]
        if not os.path.exists(path):
            continue
        data = load_page_json(path)
        touched = False
        for no, b in enumerate(data.get("bubbles", []), start=1):
            before = b.get("translation") or ""
            if b.get("skip") or not before.strip():
                continue
            after = pattern.sub(substitute, before)
            if after == before:
                continue
            key = f"{page.id}:{b['bubble_id']}"
            changes.append({"key": key, "page_id": page.id, "bubble_id": b["bubble_id"], "chapter": chapter.number,
                            "file_name": page.file_name, "bubble_no": no, "source": b.get("text", ""),
                            "before": before, "after": after, "reviewed": bubble_reviewed(b)})
            if apply and (wanted is None or key in wanted):
                if b.get("confirmed_translation") == before:
                    b["confirmed_translation"] = after       # reviewed with the old text: still reviewed
                b["translation"] = after
                b.setdefault("bulk_edits", []).append({"at": now, "find": find, "replace": replace})
                touched = True
        if touched:
            write_page_json(path, data)
    return changes
