
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
from utils.bubble_interior import detect_bubble_interior   # one shared flood-fill implementation (also used by inpainting)
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
    orig_box = to_original_coords(b["box_coords"])
    xs = [p[0] for p in orig_box]
    ys = [p[1] for p in orig_box]
    cx, cy = int(sum(xs) / len(xs)), int(sum(ys) / len(ys))
    ocr_bbox = (int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys)))
    mask, bbox = detect_bubble_interior(image_original, (cx, cy), ocr_bbox)

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
    return clamped


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


def fit_text(text: str, font_path: str, target_w: int, target_h: int, font_size: int | None = None) -> dict | None:
    """
    Lines + font for `text` in a target_w x target_h area: {"lines", "font", "line_h", "gap", "total_h", "size",
    "overflow"}. font_size None = automatic: the largest size from 40 px down whose word wrap fits (smaller sizes with
    words split at Polish syllables as the fallback). A fixed font_size (Lettering) is used as it is, even when the
    block is taller than the area ("overflow": True - the editor shows it).
    """
    words = text.split()
    if not words or target_w <= 10 or target_h <= 10:
        return None

    if font_size:
        font = _load_font(font_path, int(font_size))
        lines = _wrap_text_with_splitting(words, font, target_w)
        line_h, gap, total_h = _block_metrics(font, int(font_size), len(lines), min_gap=1)
        return {"lines": lines, "font": font, "line_h": line_h, "gap": gap, "total_h": total_h,
                "size": int(font_size), "overflow": total_h > target_h}

    size = 40                       # Starting, large font size
    while size > 8:                 # Algorithm for adjusting size and breaking lines
        font = _load_font(font_path, size)
        lines, current, too_long = [], [], False
        for word in words:
            if font.getbbox(word)[2] - font.getbbox(word)[0] > target_w:
                too_long = True     # a single word wider than the whole area: smaller font
                break
            test_line = " ".join(current + [word]) if current else word
            if font.getbbox(test_line)[2] - font.getbbox(test_line)[0] <= target_w:
                current.append(word)
            elif current:
                lines.append(" ".join(current))
                current = [word]
            else:
                lines.append(word)
                current = []
        if not too_long:
            if current:
                lines.append(" ".join(current))
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
        lines = _wrap_text_with_splitting(words, font, target_w)
        line_h, gap, total_h = _block_metrics(font, size, len(lines), min_gap=1)
        if total_h <= target_h or size <= absolute_min:
            return {"lines": lines, "font": font, "line_h": line_h, "gap": gap, "total_h": total_h,
                    "size": size, "overflow": total_h > target_h}
        size -= 1


ALIGNS = ("center", "left", "right")
STROKE_WIDTHS = {"none": 0, "thin": 2, "thick": 4}     # white outline around the letters (text over artwork)


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
    return out


def plan_bubble(image_original: np.ndarray, b: dict, img_w: int, img_h: int, font_path: str,
                to_original_coords=lambda box: box, auto_area=None) -> dict | None:
    """
    Everything needed to draw one bubble's text: area, lines, font, alignment, angle, outline. The area is the user's
    Lettering box when there is one (b["layout"]["box"]), else auto_text_area(). None = nothing to draw.
    """
    text = (b.get("translation") or b["text"]).strip()
    if get_setting("typesetting_uppercase", True):
        text = text.upper()          # manga lettering is upper case; switch off for sentence-case lettering
    layout = _clean_layout(b.get("layout"))
    tilt = b.get("text_rect")                       # tilted original text (map label): [cx, cy, w, h, angle]
    if "box" in layout:
        area = _clamp_bbox(*layout["box"], img_w, img_h, min_size=8)
    elif auto_area is not None:
        area = auto_area
    elif tilt:
        area = tilted_area(tilt, img_w, img_h)
    else:
        area = auto_text_area(image_original, b, img_w, img_h, to_original_coords)
    if area is None:
        return None
    x1, y1, x2, y2 = area
    fit = fit_text(text, font_path, x2 - x1, y2 - y1, layout.get("font_size"))
    if fit is None:
        print(f"[BUBBLE SKIP] bubble_id={b.get('bubble_id')}: area too small ({x2 - x1}x{y2 - y1}px) - text not drawn")
        return None
    if fit["overflow"]:
        print(f"[BUBBLE OVERFLOW] bubble_id={b.get('bubble_id')}: text does not fit at {fit['size']}px "
              f"({fit['total_h']}px > {y2 - y1}px). Text: {text!r}")
    angle = layout["angle"] if "angle" in layout else (tilt[4] if tilt else 0.0)
    return {"bubble_id": b.get("bubble_id"), "box": area, "manual_box": "box" in layout,
            "manual_size": "font_size" in layout, "angle": angle, "manual_angle": "angle" in layout,
            "align": layout.get("align", "center"), "stroke": layout.get("stroke", "none"), **fit}


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
    stroke = STROKE_WIDTHS.get(plan["stroke"], 0)
    # automatic size: as before Lettering (an overflowing block starts at the top); a fixed size the user chose is
    # centred on the area even when it overflows, so the text grows evenly up and down
    y = y1 + ((h - plan["total_h"]) // 2 if plan["manual_size"] else max(0, (h - plan["total_h"]) // 2))
    for line in plan["lines"]:
        bbox = plan["font"].getbbox(line)
        line_w = bbox[2] - bbox[0]
        if plan["align"] == "left":
            x = x1
        elif plan["align"] == "right":
            x = x1 + w - line_w
        else:
            x = x1 + max(0, (w - line_w) // 2)
        draw.text((x, y), line, fill=(0, 0, 0), font=plan["font"],
                  stroke_width=stroke, stroke_fill=(255, 255, 255) if stroke else None)
        y += plan["line_h"] + plan["gap"]


def draw_plan(pil_img: Image.Image, plan: dict) -> None:
    """Draw one planned bubble onto the page. A rotated one is drawn on a transparent layer, turned and pasted."""
    x1, y1, x2, y2 = plan["box"]
    w, h = x2 - x1, y2 - y1
    if not plan["angle"]:
        _draw_lines(ImageDraw.Draw(pil_img), plan, x1, y1, w, h)
        return
    pad = max(w, h) // 2 + plan["size"]                 # room for overflow and the outline before turning
    layer = Image.new("RGBA", (w + 2 * pad, h + 2 * pad), (0, 0, 0, 0))
    _draw_lines(ImageDraw.Draw(layer), plan, pad, pad, w, h)
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
    autos = {}
    for i, b in enumerate(bubbles):
        if "box" not in _clean_layout(b.get("layout")) and not b.get("text_rect"):
            autos[i] = auto_text_area(image_original, b, img_w, img_h, to_original_coords)
    keys = [i for i in autos if autos[i] is not None]
    shared = {i for i in keys for j in keys if i != j and _overlap_of_smaller(autos[i], autos[j]) >= SHARED_AREA_OVERLAP}
    for i in shared:
        autos[i] = _own_part(bubbles[i], autos[i], img_w, img_h, to_original_coords)
    plans = []
    for i, b in enumerate(bubbles):
        if i in autos and autos[i] is None:
            continue                                                  # mostly outside the page: not drawn
        plan = plan_bubble(image_original, b, img_w, img_h, font_path, to_original_coords, auto_area=autos.get(i))
        if plan is not None:
            plans.append(plan)
    return plans


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
