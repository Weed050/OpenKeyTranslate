
# backend/services/project_prefs.py

"""
Per-project choices that belong to the whole series, not to one page:

  bubble_order     reading order (ttb / rtl / ltr) for pages OCR'd from now on - the editor's "Use this order for the
                   whole project". Pages already processed keep their own order (page JSON "bubble_order").
  lettering_style  font, bold / italic, letter colour and outline used for every bubble that has no style of its own
                   (Lettering "Use in whole project") - in the editor, in the preview and in every export.

Why a file in the project folder (<project>/project_prefs.json, next to export_log.json) and not settings.json: the
global setting is ONE value for every project (a right-to-left manga next to a left-to-right webtoon), and it travels
with the project folder (backup / another PC). Before 2026-11-05:
  - "Use this order for the whole project" sorted only the pages processed so far; pages prefetched in the background
    afterwards were OCR'd in the global order again, so the editor "forgot" right to left;
  - the Lettering style "carried over" in the browser (localStorage) only to bubbles the user happened to open - the
    rest of the project was rendered and exported in the default font.
"""

import json
import os
import tempfile

FILE_NAME = "project_prefs.json"
ORDERS = ("ttb", "rtl", "ltr")
STYLE_KEYS = ("font", "bold", "italic", "stroke", "color")


def _path(project_dir: str) -> str:
    return os.path.join(project_dir, FILE_NAME)


def load(project_dir: str | None) -> dict:
    """Everything stored for the project ({} when nothing is, or the file is unreadable)."""
    if not project_dir:
        return {}
    try:
        with open(_path(project_dir), "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save(project_dir: str, patch: dict) -> dict:
    """Merge `patch` into the stored prefs (a None value removes the key); atomic write. Returns the new prefs."""
    data = load(project_dir)
    for key, value in patch.items():
        if value is None or value == {}:
            data.pop(key, None)
        else:
            data[key] = value
    os.makedirs(project_dir, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=project_dir, prefix=".prefs_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, _path(project_dir))
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
    return data


def bubble_order(project_dir: str | None) -> str | None:
    value = load(project_dir).get("bubble_order")
    return value if value in ORDERS else None


def clean_style(raw) -> dict:
    """
    The style keys that differ from the built-in defaults (default font, regular, black letters, no outline) - the
    same "only non-default values" rule as a bubble's own layout (services/bubble_edit.set_layout).
    """
    if not isinstance(raw, dict):
        return {}
    out = {}
    font = raw.get("font")
    if isinstance(font, str) and font.strip() and len(font) <= 200:
        out["font"] = font.strip()
    for key in ("bold", "italic"):
        if raw.get(key) is True:
            out[key] = True
    if raw.get("stroke") in ("thin", "thick"):
        out["stroke"] = raw["stroke"]
    if raw.get("color") == "white":
        out["color"] = "white"
    return out


def lettering_style(project_dir: str | None) -> dict:
    """The project's lettering style (only non-default keys), {} = the built-in defaults / Settings font."""
    return clean_style(load(project_dir).get("lettering_style"))


def with_style(layout: dict | None, style: dict) -> dict | None:
    """A bubble's layout on top of the project style: the bubble's own keys win (also an explicit default such as
    font "" or color "black", which set_layout stores only when the project style says otherwise)."""
    if not style:
        return layout
    merged = {**style, **(layout or {})}
    return merged or None
