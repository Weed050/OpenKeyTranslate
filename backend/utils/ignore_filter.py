
# backend/utils/ignore_filter.py

"""
Ignore-list for OCR bubbles that are not worth translating
___________________________________________________________

Scanlation pages carry text that is not story dialogue: the group's
watermark ("SOMETHINGSCANS.COM"), promo lines ("READ AT ... FOR THE FASTEST
RELEASES"), Discord links. Left alone, each one costs a translation slot,
shows up as a bubble in the editor and pollutes the translation logs the
thesis statistics are computed from.

A bubble is dropped when its text matches ANY pattern in
core.config.OCR_IGNORE_PATTERNS (settings.json -> "ocr_ignore_patterns",
editable on the Settings page). Patterns are regular expressions matched
case-insensitively with re.search, i.e. "contains" - anchor with ^...$ to
require a whole-text match (for example r"^\\W*\\d+\\W*$" drops number-only
bubbles such as page numbers).

Only the BUBBLE is dropped (this runs right after bubble grouping, next to
merge_boxes.filter_noise_bubbles). Its text lines stay in `lines`, so
inpainting still erases the text from the page exactly as before - this
filter changes what is translated and listed, not the cleaned image.

Caveat of "contains" matching: if OCR ever glued a watermark line onto a real
dialogue bubble, the whole bubble would be dropped. In practice watermarks
sit away from speech bubbles and are grouped on their own.
"""

import re

from core.config import get_setting


def compile_patterns(patterns) -> list[re.Pattern]:
    """Compile pattern strings case-insensitively; invalid ones are skipped with a log line, never fatal."""
    compiled = []
    for pattern in patterns or []:
        try:
            compiled.append(re.compile(pattern, re.IGNORECASE))
        except re.error as e:
            print(f"[IGNORE FILTER] skipping invalid pattern {pattern!r}: {e}")
    return compiled


_cache: dict = {"patterns": None, "compiled": []}


def current_patterns() -> list[re.Pattern]:
    """Compiled LIVE patterns from settings (recompiled only when the list changed) - no restart needed."""
    patterns = tuple(get_setting("ocr_ignore_patterns", []) or [])
    if _cache["patterns"] != patterns:
        _cache["patterns"] = patterns
        _cache["compiled"] = compile_patterns(patterns)
    return _cache["compiled"]


def is_ignored_text(text: str, compiled: list[re.Pattern] | None = None) -> bool:
    """True if `text` matches any ignore pattern (the configured ones unless `compiled` is given)."""
    active = current_patterns() if compiled is None else compiled
    return any(rx.search(text or "") for rx in active)


def filter_ignored_bubbles(bubbles: list[dict], compiled: list[re.Pattern] | None = None) -> list[dict]:
    """
    Drop bubbles whose text matches an ignore pattern (see module docstring).
    Each dropped bubble is printed, so the app log shows what was filtered.
    """
    kept = []
    for bubble in bubbles:
        if is_ignored_text(bubble.get("text", ""), compiled):
            print(f"[IGNORE FILTER] dropped {bubble.get('bubble_id')}: {bubble.get('text')!r}")
        else:
            kept.append(bubble)
    return kept