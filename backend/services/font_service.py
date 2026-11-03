# backend/services/font_service.py

"""
Fonts for the lettering (utils/typesetting.py): the user's own fonts plus the fonts installed on the system.

Why a service: the typesetter used one font file for the whole page (setting typesetting_font). Lettering mode lets the
user pick a font per bubble, with bold / italic, so the backend has to know which families exist, which files make up a
family's styles, and - for a Polish translation - which fonts can draw the Polish letters at all (many comic fonts only
cover ASCII and show empty boxes for ą ę ł ...).

  <app root>/fonts/        the user's fonts (Settings > Fonts uploads here; any .ttf / .otf / .ttc dropped in works too)
  system font folders      Windows Fonts, /usr/share/fonts, ~/.fonts, /Library/Fonts ...

A family is addressed by its NAME (stored in the page JSON bubble "layout": {"font": "Comic Neue", "bold": true}), so a
page keeps working when the file moves or the same font is installed on another computer. resolve() turns name + style
into a file; a missing Bold / Italic file is imitated by the typesetter (thicker stroke / slanted layer).
The scan result is cached in <app root>/fonts_index.json (file path + mtime), the first scan of ~300 system fonts takes
a second or two.
"""

import json
import os
import re
import sys

from PIL import Image, ImageChops, ImageFont

from core.config import APP_ROOT_DIR

FONTS_DIR = os.path.join(APP_ROOT_DIR, "fonts")
"""@private user font folder (under the user's app data)"""
_INDEX_FILE = os.path.join(APP_ROOT_DIR, "fonts_index.json")
FONT_EXTS = (".ttf", ".otf", ".ttc")
MAX_FONT_BYTES = 30 * 1024 * 1024
POLISH_TEST = "ąćęłńóśźżĄĆĘŁŃÓŚŹŻ"

_cache: dict | None = None


def system_font_dirs() -> list[str]:
    if sys.platform.startswith("win"):
        win = os.environ.get("WINDIR", r"C:\Windows")
        dirs = [os.path.join(win, "Fonts"), os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "Windows", "Fonts")]
    elif sys.platform == "darwin":
        dirs = ["/System/Library/Fonts", "/Library/Fonts", os.path.expanduser("~/Library/Fonts")]
    else:
        dirs = ["/usr/share/fonts", "/usr/local/share/fonts", os.path.expanduser("~/.fonts"), os.path.expanduser("~/.local/share/fonts")]
    return [d for d in dirs if d and os.path.isdir(d)]


def _font_files(folder: str):
    for root, _, files in os.walk(folder):
        for name in files:
            if name.lower().endswith(FONT_EXTS):
                yield os.path.join(root, name)


def _glyph_missing(font, ch: str, notdef) -> bool:
    """A character the font has no glyph for renders as the .notdef box (or nothing at all)."""
    mask = font.getmask(ch)
    if mask.size[0] == 0 or mask.size[1] == 0:
        return True
    img = Image.frombytes("L", mask.size, bytes(mask))
    return notdef is not None and img.size == notdef.size and ImageChops.difference(img, notdef).getbbox() is None


def probe_font(path: str) -> dict | None:
    """{family, style, polish} of one font file, or None when Pillow cannot open it."""
    try:
        font = ImageFont.truetype(path, 24)
        family, style = font.getname()
    except Exception:
        return None
    mask = font.getmask("\ue000")                      # private-use code point: no font has it -> the .notdef glyph
    notdef = Image.frombytes("L", mask.size, bytes(mask)) if mask.size[0] and mask.size[1] else None
    polish = not any(_glyph_missing(font, ch, notdef) for ch in POLISH_TEST)
    return {"family": (family or os.path.splitext(os.path.basename(path))[0]).strip(), "style": (style or "Regular").strip(),
            "polish": polish}


def _style_key(style: str) -> str | None:
    s = style.lower()
    bold = bool(re.search(r"bold|black|heavy", s))
    italic = bool(re.search(r"italic|oblique", s))
    if re.search(r"light|thin|medium|semi|demi|extra|ultra|condensed|narrow|wide|expanded", s):
        return None                                    # other weights / widths: not one of the four basic styles
    return {(False, False): "regular", (True, False): "bold", (False, True): "italic", (True, True): "bold_italic"}[(bold, italic)]


def _load_index() -> dict:
    try:
        with open(_INDEX_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save_index(index: dict) -> None:
    try:
        os.makedirs(os.path.dirname(_INDEX_FILE), exist_ok=True)
        with open(_INDEX_FILE, "w", encoding="utf-8") as f:
            json.dump(index, f)
    except OSError:
        pass


def list_families(refresh: bool = False) -> list[dict]:
    """
    [{"family", "source": "user" | "system", "polish", "styles": {"regular"|"bold"|"italic"|"bold_italic": path}}],
    user fonts first, then by name. A family whose regular file is missing uses its first style as the regular one.
    """
    global _cache
    if _cache is not None and not refresh:
        return _cache
    os.makedirs(FONTS_DIR, exist_ok=True)
    old = _load_index()
    index = {}
    sources = [(FONTS_DIR, "user")] + [(d, "system") for d in system_font_dirs()]
    families: dict[tuple, dict] = {}
    for folder, source in sources:
        for path in _font_files(folder):
            try:
                mtime = os.path.getmtime(path)
            except OSError:
                continue
            hit = old.get(path)
            info = hit["info"] if hit and hit.get("mtime") == mtime else probe_font(path)
            index[path] = {"mtime": mtime, "info": info}
            if not info:
                continue
            key = _style_key(info["style"])
            if key is None:
                continue
            fam = families.setdefault((source, info["family"].lower()), {"family": info["family"], "source": source,
                                                                         "polish": info["polish"], "styles": {}})
            fam["styles"].setdefault(key, path)
            if key == "regular":
                fam["polish"] = info["polish"]          # the family can write Polish when its regular file can
    _save_index(index)
    out = []
    seen = set()
    for (source, low), fam in sorted(families.items(), key=lambda kv: (kv[0][0] != "user", kv[1]["family"].lower())):
        if low in seen:                               # same family as a user font: the user's copy wins
            continue
        seen.add(low)
        if "regular" not in fam["styles"]:
            fam["styles"]["regular"] = next(iter(fam["styles"].values()))
        out.append(fam)
    _cache = out
    return out


def find_family(name: str | None) -> dict | None:
    if not name:
        return None
    low = name.strip().lower()
    return next((f for f in list_families() if f["family"].lower() == low), None)


def resolve(name: str | None, bold: bool = False, italic: bool = False) -> tuple[str | None, bool, bool]:
    """
    (font file, fake_bold, fake_italic) for a family + style. None as the file = family unknown (the caller keeps its
    default font). A style without its own file is imitated: fake_bold / fake_italic tell the typesetter to do it.
    """
    fam = find_family(name)
    if not fam:
        return None, bool(bold), bool(italic)
    want = "bold_italic" if bold and italic else "bold" if bold else "italic" if italic else "regular"
    styles = fam["styles"]
    if want in styles:
        return styles[want], False, False
    if want == "bold_italic":
        if "bold" in styles:
            return styles["bold"], False, True
        if "italic" in styles:
            return styles["italic"], True, False
    return styles["regular"], bool(bold), bool(italic)


def save_user_font(file_name: str, data: bytes) -> dict:
    """Store an uploaded font in the user font folder after checking that it opens. Returns its {family, style, polish}."""
    name = os.path.basename(file_name or "").strip()
    if not name.lower().endswith(FONT_EXTS):
        raise ValueError("Only .ttf, .otf or .ttc font files.")
    if not data or len(data) > MAX_FONT_BYTES:
        raise ValueError("The font file is empty or larger than 30 MB.")
    os.makedirs(FONTS_DIR, exist_ok=True)
    target = os.path.join(FONTS_DIR, name)
    tmp = target + ".part"
    with open(tmp, "wb") as f:
        f.write(data)
    info = probe_font(tmp)
    if not info:
        os.remove(tmp)
        raise ValueError("This file is not a font Pillow can open.")
    os.replace(tmp, target)
    list_families(refresh=True)
    return {"file": name, **info}


def user_fonts() -> list[dict]:
    """The files in the user font folder: [{file, family, style, polish}]."""
    os.makedirs(FONTS_DIR, exist_ok=True)
    out = []
    for path in sorted(_font_files(FONTS_DIR)):
        info = probe_font(path) or {"family": "?", "style": "", "polish": False}
        out.append({"file": os.path.relpath(path, FONTS_DIR), **info})
    return out


def delete_user_font(file_name: str) -> None:
    target = os.path.normpath(os.path.join(FONTS_DIR, file_name))
    if not target.startswith(os.path.normpath(FONTS_DIR) + os.sep) or not os.path.isfile(target):
        raise ValueError("Font file not found in the user font folder.")
    os.remove(target)
    list_families(refresh=True)
