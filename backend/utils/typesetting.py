
# backend/utils/typesetting.py

"""
Handles text wrapping, word breaking, and space aware typesetting for manga speech bubbles.

This module is responsible for ensuring that translated text fits naturally within
the borders of actual, physical speech bubbles (rather than logical clusters grouped
solely based on text proximity).

Core functionalities:
1. Bubble Interior Detection: Uses a morphological flood-fill algorithm starting
   from the OCR text centroid to map the exact physical boundaries (walls) of the
   speech bubble, ignoring dark artwork and panel borders.
2. Dynamic Text Formatting: Splits, wraps, and hyphenates words using Pyphen.
   Crucially, this is configured specifically to respect Polish hyphenation rules
   (using the 'pl_PL' dictionary pattern) to ensure that the newly wrapped text
   remains linguistically correct and visually balanced within the bubble's geometry.
"""

from typing import List
from PIL import Image, ImageDraw, ImageFont
import cv2
import numpy as np
import os
from core.config import TYPESETTING_PADDING_RATIO, get_setting
from utils.log_once import print_once      # the same line not on every Lettering redraw
from utils.bubble_interior import find_interior   # one shared flood-fill implementation (also used by inpainting)
import pyphen


_hyphen_dict = pyphen.Pyphen(lang='pl_PL')

def _clamp_bbox(x1, y1, x2, y2, img_w, img_h, min_size=15):
    """
    Clamps the bounding box coordinates to the actual boundaries of the image canvas
    we are drawing on to prevent out-of-bounds rendering exceptions.
    """
    cx1 = max(0, min(x1, img_w - 1))
    cy1 = max(0, min(y1, img_h - 1))
    cx2 = max(0, min(x2, img_w))
    cy2 = max(0, min(y2, img_h))

    if cx2 - cx1 < min_size or cy2 - cy1 < min_size:
        return None
    return cx1, cy1, cx2, cy2


def _syllables(word: str) -> List[str]:
    """Splits a word into syllables based on Polish hyphenation rules."""
    # Use a null byte (\x00) as a temporary delimiter for hyphenation points
    hyphenated = _hyphen_dict.inserted(word, hyphen="\x00")
    return hyphenated.split("\x00")

def _split_chars_to_fit(word: str, font: ImageFont.ImageFont, max_w: int) -> List[str]:
    """
    Brutal fallback: splits character-by-character when even a single syllable
    is too wide for the target width.
    """
    chunks = []
    current = ""
    for ch in word:
        test = current + ch
        # Calculate width using the bounding box
        w = font.getbbox(test)[2] - font.getbbox(test)[0]

        # If it fits, or if the current buffer is empty (to prevent infinite loops), accumulate
        if w <= max_w or not current:
            current = test
        else:
            chunks.append(current)
            current = ch

    if current:
        chunks.append(current)
    return chunks


def _split_word_to_fit(
        word: str,
        font: ImageFont.ImageFont,
        max_w: int) -> List[str]:
    """
    Splits an oversized word into fragments that fit within max_w.

    First, it attempts to split at syllable boundaries according to Polish rules
    (appending a hyphen at the end of each fragment except the last) — resulting
    in proper typographic layout.

    Falls back to character-by-character splitting only if the word has no recognized
    syllables (e.g., foreign proper nouns, digit strings) or if a single syllable
    itself is wider than max_w.
    """
    # Guard clause for invalid or zero width bounding box
    if max_w <= 0:
        return [word]

    word_w = font.getbbox(word)[2] - font.getbbox(word)[0]
    if word_w <= max_w:
        return [word]

    syllables = _syllables(word)
    chunks = []
    current = ""

    # Attempt syllable-based hyphenation
    for i, syl in enumerate(syllables):
        is_last = (i == len(syllables) - 1)

        # Test line width including the hyphen character if it's not the last syllable
        candidate = current + syl + ("" if is_last else "-")
        cw = font.getbbox(candidate)[2] - font.getbbox(candidate)[0]

        if cw <= max_w or not current:
            current += syl
        else:
            # Commit current chunk with a trailing hyphen and start a new one
            chunks.append(current + "-")
            current = syl

    if current:
        chunks.append(current)

    # Emergency fallback: if any syllable fragment is still too wide, split char-by-char
    final_chunks = []
    for chunk in chunks:
        cw = font.getbbox(chunk)[2] - font.getbbox(chunk)[0]
        if cw <= max_w:
            final_chunks.append(chunk)
        else:
            # Character-by-character split for extreme cases
            final_chunks.extend(_split_chars_to_fit(chunk, font, max_w))

    return final_chunks


_FONT_CANDIDATES = ("arial.ttf", "Arial.ttf", "DejaVuSans.ttf", "LiberationSans-Regular.ttf", "Helvetica.ttc")


def resolve_font_path() -> str:
    """
    Font file for the translated text: setting typesetting_font (a .ttf/.otf path, e.g. a comic font that has the Polish
    letters) when it exists, else the first common system font that Pillow can open. Before: "arial.ttf" hard-coded, so
    machines without Arial silently fell back to Pillow's bitmap font - which has no Polish diacritics.
    """
    configured = (get_setting("typesetting_font", "") or "").strip()
    if configured and os.path.isfile(configured):
        return configured
    if configured:                                   # a family name (Settings > Fonts), see services/font_service.py
        try:
            from services.font_service import resolve
            path, _, _ = resolve(configured)
            if path:
                return path
        except Exception as e:
            print(f"[FONT] {configured!r}: {type(e).__name__}: {e} - using a system default")
    for name in _FONT_CANDIDATES:
        try:
            ImageFont.truetype(name, 12)
            return name
        except OSError:
            continue
    return "arial.ttf"        # unchanged last resort: the loader below falls back to the default font


def auto_text_area(image_original: np.ndarray, b: dict, img_w: int, img_h: int, to_original_coords=lambda box: box):
    """
    Where the translation goes when the user has not placed it (Lettering): the bubble interior found by the shared
    flood fill, 10 % in from its edges, or - without an interior - the OCR box grown a little; then the uniform
    TYPESETTING_PADDING_RATIO margin and clamping to the canvas. (x1, y1, x2, y2) or None (mostly outside the page).
    """
    return auto_text_shape(image_original, b, img_w, img_h, to_original_coords)[0]


SHAPE_MARGIN = 0.07         # shape fit: letters keep this share of the bubble's smaller side away from the outline
SHAPE_MIN_SIZE = 11         # below this the shape fit gives up and the rectangle (with syllable splitting) is used


def _shape_rows(interior, margin_px: int):
    """
    Per image row of the bubble: the widest run of inside pixels after shrinking the inside by margin_px -
    (y0, lefts, rights) in image coordinates, or None. An oval gives short rows at the top and bottom, long ones in
    the middle: the text can be as wide as the bubble where the bubble is wide.
    """
    hull = interior.hull
    if margin_px > 0:
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * margin_px + 1, 2 * margin_px + 1))
        hull = cv2.erode(hull, k)
    inside = hull > 0
    rows = np.flatnonzero(inside.any(axis=1))
    if len(rows) < 10:
        return None
    lefts, rights = [], []
    for r in range(rows[0], rows[-1] + 1):
        line = np.concatenate(([False], inside[r], [False]))
        edges = np.flatnonzero(np.diff(line.astype(np.int8)))
        starts, ends = edges[::2], edges[1::2]
        if not len(starts):
            lefts.append(0)
            rights.append(0)
            continue
        i = int(np.argmax(ends - starts))
        lefts.append(int(starts[i]))
        rights.append(int(ends[i]))
    ox, oy = interior.origin
    return oy + int(rows[0]), np.array(lefts) + ox, np.array(rights) + ox


def auto_text_shape(image_original: np.ndarray, b: dict, img_w: int, img_h: int, to_original_coords=lambda box: box):
    """auto_text_area() plus the bubble's shape: (area, shape). shape = (y0, lefts, rights) per row (see _shape_rows) or
    None (no interior found - the OCR box is used)."""
    orig_box = to_original_coords(b["box_coords"])
    xs = [p[0] for p in orig_box]
    ys = [p[1] for p in orig_box]
    cx, cy = int(sum(xs) / len(xs)), int(sum(ys) / len(ys))
    ocr_bbox = (int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys)))
    why: list = []
    interior = find_interior(image_original, (cx, cy), ocr_bbox, why=why)
    if interior is None:
        print_once(f"[BUBBLE GUARD] bubble_id={b.get('bubble_id')}: {why[0] if why else 'rejected'} - falling back to the OCR bounding box.")
        bbox, shape = None, None
    else:
        bbox = interior.bbox
        side = min(bbox[2] - bbox[0], bbox[3] - bbox[1])
        shape = _shape_rows(interior, max(3, int(side * SHAPE_MARGIN)))

    if bbox:
        bx1, by1, bx2, by2 = bbox
        mx = int((bx2 - bx1) * 0.10)
        my = int((by2 - by1) * 0.10)
        x1, y1, x2, y2 = bx1 + mx, by1 + my, bx2 - mx, by2 - my
    else:
        x1, x2 = ocr_bbox[0], ocr_bbox[2]
        y1, y2 = ocr_bbox[1], ocr_bbox[3]
        w, h = x2 - x1, y2 - y1
        x1 -= int(w * 0.175)
        x2 += int(w * 0.175)
        y1 -= int(h * 0.20)
        y2 += int(h * 0.20)

    # Uniform margin - the same rule for both paths, a single knob for tuning
    pad_x = int((x2 - x1) * TYPESETTING_PADDING_RATIO)
    pad_y = int((y2 - y1) * TYPESETTING_PADDING_RATIO)
    clamped = _clamp_bbox(x1 + pad_x, y1 + pad_y, x2 - pad_x, y2 - pad_y, img_w, img_h)
    if clamped is None:
        print(f"[BUBBLE CLAMP FAIL] bubble_id={b.get('bubble_id')}: text area is mostly outside the canvas - not drawn")
    return clamped, shape


def fit_text_shape(text: str, font_path: str, shape) -> dict | None:
    """
    Like fit_text, but every line gets the width the bubble has at that line's height (shape from _shape_rows), the
    block centred vertically in the shape - the classic lettering of an oval bubble: short lines at the top and the
    bottom, long ones in the middle. The largest size from 40 px down where the words fit WITHOUT splitting a word;
    None when nothing fits down to SHAPE_MIN_SIZE (the caller keeps the rectangle). Extra keys: "box" (the shape's
    bounding box), "slots" [(dx, width)] per line and "top" (dy of the first line), both relative to "box".
    """
    if "\n" in text:
        return None                  # the user's own line breaks (layout "lines"): the rectangle keeps them
    words = text.split()
    if not words:
        return None
    y0, lefts, rights = shape
    widths = rights - lefts
    used = np.flatnonzero(widths > 0)
    if len(used) < 10:
        return None
    H = int(used[-1]) + 1
    box = (int(lefts[used].min()), y0, int(rights[used].max()), y0 + H)
    for size in range(40, SHAPE_MIN_SIZE - 1, -2):
        font = _load_font(font_path, size)
        word_w = [font.getbbox(w)[2] - font.getbbox(w)[0] for w in words]
        line_h, gap, _ = _block_metrics(font, size, 1)
        max_n = (H + gap) // (line_h + gap)
        for n in range(1, min(max_n, len(words)) + 1):
            total = n * line_h + (n - 1) * gap
            top = (H - total) // 2
            slots = []
            for i in range(n):
                r1 = top + i * (line_h + gap)
                lo, hi = int(lefts[r1:r1 + line_h].max()), int(rights[r1:r1 + line_h].min())
                slots.append((lo, max(0, hi - lo)))
            lines, k = [], 0
            for lo, w in slots:
                cur = []
                while k < len(words):
                    test = " ".join(cur + [words[k]])
                    tw = font.getbbox(test)[2] - font.getbbox(test)[0] if cur else word_w[k]
                    if tw > w:
                        break
                    cur.append(words[k])
                    k += 1
                lines.append(" ".join(cur))
            if k == len(words) and all(lines):
                return {"lines": lines, "font": font, "line_h": line_h, "gap": gap, "total_h": total, "size": size,
                        "overflow": False, "box": box, "slots": [(lo - box[0], w) for lo, w in slots], "top": top}
    return None


def _load_font(font_path: str, size: int):
    try:
        return ImageFont.truetype(font_path, size)
    except IOError:
        return ImageFont.load_default()


def _block_metrics(font, font_size: int, n_lines: int, min_gap: int = 0):
    sample_bbox = font.getbbox("Ay_gżł")
    line_h = sample_bbox[3] - sample_bbox[1]
    gap = max(min_gap, int(font_size * 0.15))
    return line_h, gap, (n_lines * line_h) + ((n_lines - 1) * gap)


def _wrap_whole_words(words, font, target_w):
    """Greedy word wrap that never splits a word; None when one word alone is wider than the area."""
    lines, current = [], []
    for word in words:
        if font.getbbox(word)[2] - font.getbbox(word)[0] > target_w:
            return None             # a single word wider than the whole area: smaller font
        test_line = " ".join(current + [word]) if current else word
        if font.getbbox(test_line)[2] - font.getbbox(test_line)[0] <= target_w:
            current.append(word)
        elif current:
            lines.append(" ".join(current))
            current = [word]
        else:
            lines.append(word)
            current = []
    if current:
        lines.append(" ".join(current))
    return lines


def fit_text(text: str, font_path: str, target_w: int, target_h: int, font_size: int | None = None) -> dict | None:
    """
    Lines + font for `text` in a target_w x target_h area: {"lines", "font", "line_h", "gap", "total_h", "size",
    "overflow"}. font_size None = automatic: the largest size from 40 px down whose word wrap fits (smaller sizes with
    words split at Polish syllables as the fallback). A fixed font_size (Lettering) is used as it is, even when the
    block is taller than the area ("overflow": True - the editor shows it).
    A "\n" in `text` is a line break the user set in Lettering (layout "lines", see plan_bubble): each such line is
    wrapped on its own and an empty one stays an empty line. Text without "\n" is laid out exactly as before.
    """
    forced = [p.split() for p in text.replace("\r", "").split("\n")]
    if not any(forced) or target_w <= 10 or target_h <= 10:
        return None

    def wrap_all(wrap):
        lines = []
        for words in forced:
            part = wrap(words) if words else [""]
            if part is None:
                return None
            lines.extend(part)
        return lines

    if font_size:
        font = _load_font(font_path, int(font_size))
        lines = wrap_all(lambda words: _wrap_text_with_splitting(words, font, target_w))
        line_h, gap, total_h = _block_metrics(font, int(font_size), len(lines), min_gap=1)
        return {"lines": lines, "font": font, "line_h": line_h, "gap": gap, "total_h": total_h,
                "size": int(font_size), "overflow": total_h > target_h}

    # The user's own lines first stay WHOLE (a smaller font instead of wrapping them again); only when no size down
    # to 10 px manages that, they are wrapped like any other text. Without forced breaks: one pass, as always.
    for whole in ((True, False) if len(forced) > 1 else (False,)):
        size = 40                       # Starting, large font size
        while size > 8:                 # Algorithm for adjusting size and breaking lines
            font = _load_font(font_path, size)
            lines = wrap_all(lambda words: _wrap_whole_words(words, font, target_w))
            if lines is not None and (not whole or len(lines) == len(forced)):
                line_h, gap, total_h = _block_metrics(font, size, len(lines))
                if total_h <= target_h:
                    return {"lines": lines, "font": font, "line_h": line_h, "gap": gap, "total_h": total_h,
                            "size": size, "overflow": False}
            size -= 2

    # Fallback branch: word wrapping with syllable splitting, from the minimum readable size down
    min_font_size, absolute_min = 11, 7
    size = min_font_size
    while True:
        font = _load_font(font_path, size)
        lines = wrap_all(lambda words: _wrap_text_with_splitting(words, font, target_w))
        line_h, gap, total_h = _block_metrics(font, size, len(lines), min_gap=1)
        if total_h <= target_h or size <= absolute_min:
            return {"lines": lines, "font": font, "line_h": line_h, "gap": gap, "total_h": total_h,
                    "size": size, "overflow": total_h > target_h}
        size -= 1


ALIGNS = ("center", "left", "right")
VALIGNS = ("middle", "top", "bottom")
STROKE_WIDTHS = {"none": 0, "thin": 2, "thick": 4}     # outline around the letters (text over artwork), at 24 px text
STROKE_PER_PX = {"none": 0, "thin": 0.08, "thick": 0.16}   # ... and growing with the text: thick = 1/6 of the size
TEXT_COLORS = ("black", "white")                       # the letters; the outline is always the other colour
_RGB = {"black": (0, 0, 0), "white": (255, 255, 255)}


def stroke_width(stroke: str, size: int) -> int:
    """Outline width in px for this text size: the old fixed 2 / 4 px at small sizes, proportional above (a 4 px outline
    on 40 px letters was hard to see)."""
    if not STROKE_WIDTHS.get(stroke):
        return 0
    return max(STROKE_WIDTHS[stroke], round(size * STROKE_PER_PX[stroke]))


def _clean_layout(layout) -> dict:
    """The user's Lettering choices of a bubble (page JSON "layout"), validated; {} = everything automatic."""
    if not isinstance(layout, dict):
        return {}
    out = {}
    box = layout.get("box")
    if isinstance(box, (list, tuple)) and len(box) == 4:
        try:
            x1, y1, x2, y2 = (int(round(float(v))) for v in box)
            out["box"] = (min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))
        except (TypeError, ValueError):
            pass
    try:
        if layout.get("font_size"):
            out["font_size"] = max(6, min(300, int(layout["font_size"])))
    except (TypeError, ValueError):
        pass
    if "angle" in layout:            # absent = automatic (level, or the slant of tilted original text)
        try:
            out["angle"] = float(layout.get("angle") or 0) % 360
            if out["angle"] > 180:
                out["angle"] -= 360
        except (TypeError, ValueError):
            out["angle"] = 0.0
    out["align"] = layout.get("align") if layout.get("align") in ALIGNS else "center"
    out["stroke"] = layout.get("stroke") if layout.get("stroke") in STROKE_WIDTHS else "none"
    out["valign"] = layout.get("valign") if layout.get("valign") in VALIGNS else "middle"
    out["color"] = layout.get("color") if layout.get("color") in TEXT_COLORS else "black"
    if isinstance(layout.get("font"), str) and layout["font"].strip():
        out["font"] = layout["font"].strip()
    out["bold"] = layout.get("bold") is True
    out["italic"] = layout.get("italic") is True
    lines = layout.get("lines")
    if isinstance(lines, (list, tuple)) and lines and all(isinstance(l, str) for l in lines):
        out["lines"] = [" ".join(l.split()) for l in lines][:80]
    return out


def same_letters(a: str, b: str) -> bool:
    """The same text apart from spaces, line breaks and letter case (the test for the user's own line breaks)."""
    return "".join((a or "").split()).casefold() == "".join((b or "").split()).casefold()


_FAMILY_OF: dict[str, str] = {}


def _font_for(layout: dict, default_path: str) -> tuple[str, bool, bool, str | None]:
    """
    (font file, fake_bold, fake_italic, family) of a bubble: its own font / style from Lettering, else the page default.
    Bold / italic of a family without such a file are imitated by _draw_lines / draw_plan.
    """
    name, bold, italic = layout.get("font"), layout.get("bold", False), layout.get("italic", False)
    if not (name or bold or italic):
        return default_path, False, False, None
    try:
        from services.font_service import probe_font, resolve
        if not name:                                  # B / I on the default font: find its family first
            if default_path not in _FAMILY_OF:
                info = probe_font(default_path) if os.path.isfile(default_path) else None
                _FAMILY_OF[default_path] = info["family"] if info else ""
            name = _FAMILY_OF[default_path]
        path, fake_bold, fake_italic = resolve(name, bold, italic) if name else (None, bold, italic)
    except Exception as e:
        print(f"[FONT] {name!r}: {type(e).__name__}: {e}")
        path, fake_bold, fake_italic = None, bold, italic
    return (path or default_path), fake_bold, fake_italic, name or None


def plan_bubble(image_original: np.ndarray, b: dict, img_w: int, img_h: int, font_path: str,
                to_original_coords=lambda box: box, auto_area=None, auto_shape=None) -> dict | None:
    """
    Everything needed to draw one bubble's text: area, lines, font, alignment, angle, outline. The area is the user's
    Lettering box when there is one (b["layout"]["box"]), else auto_text_area(). None = nothing to draw.
    """
    text = (b.get("translation") or b["text"]).strip()
    if get_setting("typesetting_uppercase", True):
        text = text.upper()          # manga lettering is upper case; switch off for sentence-case lettering
    layout = _clean_layout(b.get("layout"))
    # The user's own line breaks (Lettering "Lines"): used only while they still spell the bubble's text - after the
    # translation is edited they no longer match and the automatic wrap is back. A break may fall inside a word
    # (a sound effect lettered one letter per line).
    manual_lines = bool(layout.get("lines")) and same_letters("".join(layout["lines"]), text)
    if manual_lines:
        text = "\n".join(l.upper() if get_setting("typesetting_uppercase", True) else l for l in layout["lines"])
    tilt = b.get("text_rect")                       # tilted original text (map label): [cx, cy, w, h, angle]
    shape = None
    if "box" in layout:
        area = _clamp_bbox(*layout["box"], img_w, img_h, min_size=8)
    elif auto_area is not None:
        area, shape = auto_area, auto_shape
    elif tilt:
        area = tilted_area(tilt, img_w, img_h)
    else:
        area, shape = auto_text_shape(image_original, b, img_w, img_h, to_original_coords)
    if area is None:
        return None
    x1, y1, x2, y2 = area
    font_path, fake_bold, fake_italic, family = _font_for(layout, font_path)
    fit = fit_text(text, font_path, x2 - x1, y2 - y1, layout.get("font_size"))
    # oval bubble: lines as wide as the bubble at their height - used only when the size is automatic, the text is
    # level and centred vertically, and the result is LARGER than the rectangle's (never a smaller text)
    if (shape is not None and fit is not None and get_setting("typesetting_shape_fit", True)
            and "font_size" not in layout and not layout.get("angle") and layout.get("valign", "middle") == "middle"):
        shaped = fit_text_shape(text, font_path, shape)
        if shaped and (fit["overflow"] or shaped["size"] > fit["size"]):
            fit, area = shaped, (_clamp_bbox(*shaped["box"], img_w, img_h, min_size=8) or area)
            x1, y1, x2, y2 = area
    if fit is None:
        print(f"[BUBBLE SKIP] bubble_id={b.get('bubble_id')}: area too small ({x2 - x1}x{y2 - y1}px) - text not drawn")
        return None
    if fit["overflow"]:
        print_once(f"[BUBBLE OVERFLOW] bubble_id={b.get('bubble_id')}: text does not fit at {fit['size']}px "
                   f"({fit['total_h']}px > {y2 - y1}px). Text: {text!r}")
    angle = layout["angle"] if "angle" in layout else (tilt[4] if tilt else 0.0)
    return {"bubble_id": b.get("bubble_id"), "box": area, "manual_box": "box" in layout,
            "manual_size": "font_size" in layout, "angle": angle, "manual_angle": "angle" in layout,
            "manual_lines": manual_lines,
            "slots": fit.get("slots"), "top": fit.get("top"),
            "align": layout.get("align", "center"), "stroke": layout.get("stroke", "none"),
            "valign": layout.get("valign", "middle"), "color": layout.get("color", "black"), "font_name": family, "bold": layout.get("bold", False),
            "italic": layout.get("italic", False), "fake_bold": fake_bold, "fake_italic": fake_italic, **fit}


def tilted_area(rect, img_w: int, img_h: int):
    """
    Text area of tilted original text: the level box around the text's own centre, a little longer and taller than
    the text - a label stays one line unless the translation is much longer (then two, smaller) - drawn turned by the
    text's angle, so the translation follows the slant.
    """
    cx, cy, w, h = rect[:4]
    w, h = w * 1.15 + 6, max(h * 1.3, h + 6)
    return _clamp_bbox(int(cx - w / 2), int(cy - h / 2), int(cx + w / 2), int(cy + h / 2), img_w, img_h, min_size=8)


def _draw_lines(draw, plan: dict, x1: int, y1: int, w: int, h: int) -> None:
    """The plan's lines in the w x h area at (x1, y1): vertically centred, aligned as the plan says."""
    stroke = stroke_width(plan["stroke"], plan["size"])
    ink = _RGB[plan.get("color", "black")]
    edge = _RGB["white" if plan.get("color", "black") == "black" else "black"]
    # imitated bold: an outline in the text colour (none below 14 px - one pixel there already fills the letters in)
    bold_w = max(1, round(plan["size"] / 24)) if plan.get("fake_bold") and plan["size"] >= 14 else 0
    # automatic size: as before Lettering (an overflowing block starts at the top); a fixed size the user chose is
    # centred on the area even when it overflows, so the text grows evenly up and down. Top / bottom from Lettering.
    valign = plan.get("valign", "middle")
    slots = plan.get("slots")
    if slots:                                       # shape fit: every line in its own width, the block placed by fit
        y = y1 + plan["top"]
    elif valign == "top":
        y = y1
    elif valign == "bottom":
        y = y1 + h - plan["total_h"] if plan["manual_size"] else y1 + max(0, h - plan["total_h"])
    else:
        y = y1 + ((h - plan["total_h"]) // 2 if plan["manual_size"] else max(0, (h - plan["total_h"]) // 2))
    for i, line in enumerate(plan["lines"]):
        bbox = plan["font"].getbbox(line)
        line_w = bbox[2] - bbox[0]
        lx, lw = (x1 + slots[i][0], slots[i][1]) if slots else (x1, w)
        if plan["align"] == "left":
            x = lx
        elif plan["align"] == "right":
            x = lx + lw - line_w
        else:
            x = lx + max(0, (lw - line_w) // 2)
        if stroke and bold_w:                       # the outline first, the thickened letters on top of it
            draw.text((x, y), line, fill=ink, font=plan["font"], stroke_width=stroke + bold_w, stroke_fill=edge)
        draw.text((x, y), line, fill=ink, font=plan["font"],
                  stroke_width=bold_w or stroke, stroke_fill=ink if bold_w else (edge if stroke else None))
        y += plan["line_h"] + plan["gap"]


def draw_plan(pil_img: Image.Image, plan: dict) -> None:
    """Draw one planned bubble onto the page. A rotated one is drawn on a transparent layer, turned and pasted."""
    x1, y1, x2, y2 = plan["box"]
    w, h = x2 - x1, y2 - y1
    if not plan["angle"] and not plan.get("fake_italic"):
        _draw_lines(ImageDraw.Draw(pil_img), plan, x1, y1, w, h)
        return
    pad = max(w, h) // 2 + plan["size"]                 # room for overflow and the outline before turning
    layer = Image.new("RGBA", (w + 2 * pad, h + 2 * pad), (0, 0, 0, 0))
    _draw_lines(ImageDraw.Draw(layer), plan, pad, pad, w, h)
    if plan.get("fake_italic"):                         # slant the letters 12 degrees around the middle line
        k = 0.21
        layer = layer.transform(layer.size, Image.AFFINE, (1, k, -k * layer.height / 2, 0, 1, 0), resample=Image.BICUBIC)
    if plan["angle"]:
        layer = layer.rotate(-plan["angle"], resample=Image.BICUBIC, expand=True)   # positive angle = clockwise (as CSS)
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
    pil_img.paste(layer, (int(round(cx - layer.width / 2)), int(round(cy - layer.height / 2))), layer)


def _overlap_of_smaller(a, b) -> float:
    """Share of the smaller of two boxes that the other one covers (two connected bubbles: often 0.4-1.0)."""
    w = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    h = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    smaller = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1]))
    return w * h / smaller if smaller > 0 else 0.0


SHARED_AREA_OVERLAP = 0.3       # automatic text areas overlapping this much: the bubbles share one outline


def _own_part(b: dict, shared, img_w: int, img_h: int, to_original_coords):
    """The part of a shared text area around this bubble's own text: its OCR box grown a little, inside the shared area."""
    box = to_original_coords(b["box_coords"])
    xs, ys = [p[0] for p in box], [p[1] for p in box]
    x1, y1, x2, y2 = min(xs), min(ys), max(xs), max(ys)
    gx, gy = (x2 - x1) * 0.25, (y2 - y1) * 0.20
    part = (int(max(x1 - gx, shared[0])), int(max(y1 - gy, shared[1])), int(min(x2 + gx, shared[2])), int(min(y2 + gy, shared[3])))
    if part[2] - part[0] < 15 or part[3] - part[1] < 15:          # the text sticks out of the shared area: its own box
        part = (int(x1 - gx), int(y1 - gy), int(x2 + gx), int(y2 + gy))
    return _clamp_bbox(*part, img_w, img_h)


def plan_page(image_clean: np.ndarray, image_original: np.ndarray, bubbles: List[dict], to_original_coords=lambda box: box) -> list:
    """
    Plans of every bubble of the page (no drawing) - also what the editor's Lettering mode shows as text areas.
    Two bubbles in ONE outline (connected bubbles: the flood fill finds the same interior for both) used to get the
    same (or a largely overlapping) automatic area, so their texts were drawn on top of each other (Chapter_1/page_034,
    page_035). Bubbles whose automatic areas cover >= 30 % of the smaller one now each get the part around their own text.
    """
    img_h, img_w = image_clean.shape[:2]
    font_path = resolve_font_path()
    autos, shapes = {}, {}
    for i, b in enumerate(bubbles):
        if "box" not in _clean_layout(b.get("layout")) and not b.get("text_rect"):
            autos[i], shapes[i] = auto_text_shape(image_original, b, img_w, img_h, to_original_coords)
    keys = [i for i in autos if autos[i] is not None]
    shared = {i for i in keys for j in keys if i != j and _overlap_of_smaller(autos[i], autos[j]) >= SHARED_AREA_OVERLAP}
    for i in shared:
        autos[i] = _own_part(bubbles[i], autos[i], img_w, img_h, to_original_coords)
        shapes[i] = None                                              # a part of a shared outline: the rectangle
    plans = []
    for i, b in enumerate(bubbles):
        if i in autos and autos[i] is None:
            continue                                                  # mostly outside the page: not drawn
        plan = plan_bubble(image_original, b, img_w, img_h, font_path, to_original_coords, auto_area=autos.get(i),
                           auto_shape=shapes.get(i))
        if plan is not None:
            if "color" not in (b.get("layout") or {}) and _dark_background(image_clean, plan["box"]):
                plan["color"], plan["auto_color"] = "white", True
            plans.append(plan)
    return plans


DARK_BACKGROUND_MAX = 80        # median grey (0-255) of the cleaned text area below this: white letters by default


def _dark_background(image_clean: np.ndarray, box) -> bool:
    """
    The cleaned text area is dark (white text on a black caption box / night panel, Chapter_3/page_022): black letters
    would be invisible, so a bubble without its own letter colour gets white ones. The median of the inner 80 % of the
    area - a few dark lines of artwork inside a white bubble do not flip it.
    """
    try:
        x1, y1, x2, y2 = (int(v) for v in box)
        dx, dy = (x2 - x1) // 10, (y2 - y1) // 10
        crop = image_clean[max(0, y1 + dy):max(0, y2 - dy), max(0, x1 + dx):max(0, x2 - dx)]
        if crop.size == 0:
            return False
        grey = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop
        return float(np.median(grey)) < DARK_BACKGROUND_MAX
    except Exception:
        return False


def draw_translated_text_on_clean_image(
        image_clean: np.ndarray,
        image_original: np.ndarray,
        bubbles: List[dict],
        to_original_coords
) -> np.ndarray:
    """
    Draws translated text using the Pillow (PIL) library.

    Provides full, native support for Polish diacritic characters (ą, ć, ę, ł, ń, ó, ś, ź, ż),
    enforces UPPERCASE formatting typical for manga lettering, and strictly respects
    the detected bubble boundaries (bounding box guard). A bubble's "layout" (editor Lettering mode) overrides the
    area, font size, alignment, angle and outline; without it the result is the same as before Lettering existed.
    """
    pil_img = Image.fromarray(cv2.cvtColor(image_clean, cv2.COLOR_BGR2RGB))
    for plan in plan_page(image_clean, image_original, bubbles, to_original_coords):
        draw_plan(pil_img, plan)
    return cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)


def _wrap_text_with_splitting(words, font, target_w):
    """Wraps words to fit target_w, breaking down individual words that are too long into pieces."""
    lines = []
    current_line = []

    for word in words:
        # Calculate the width of the current word using its bounding box
        word_w = font.getbbox(word)[2] - font.getbbox(word)[0]

        # Case 1: The single word itself is wider than the target width
        if word_w > target_w:
            if current_line:
                lines.append(" ".join(current_line))
                current_line = []
            # Force split the oversized word and add the pieces directly to lines
            lines.extend(_split_word_to_fit(word, font, target_w))
            continue

        # Case 2: Test if the word fits into the current line
        test_line = " ".join(current_line + [word]) if current_line else word
        line_w = font.getbbox(test_line)[2] - font.getbbox(test_line)[0]

        if line_w <= target_w:
            # The word fits perfectly, append it to the current line
            current_line.append(word)
        else:
            # The word doesn't fit, so we close the current line (if it exists)
            if current_line:
                lines.append(" ".join(current_line))
                current_line = [word]  # Start a new line with the current word
            else:
                # Fallback: if the current line was empty, just append the word as a full line
                lines.append(word)
                current_line = []

    # Append any remaining text left in the buffer
    if current_line:
        lines.append(" ".join(current_line))

    return lines
