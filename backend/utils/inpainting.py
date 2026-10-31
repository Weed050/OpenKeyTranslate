
#  backend/utils/inpainting.py

"""
Erasing the original text from manga/webtoon pages.

WHAT WAS WRONG (seen on real pages): the mask was the OCR text-line rectangle, dilated a few pixels, filled by OpenCV
TELEA. Consequences:
  * the OCR box is often shorter than bold lettering, so the top half of "THIS IS" stayed and the lower half became a
    grey smear (TELEA copies the dark remains next to it);
  * a box near the bubble outline covers part of the outline; TELEA then drags the dark outline INTO the erased area
    (the "blurred edge");
  * big rectangles over screentone / gradients smear.

WHAT IT DOES NOW (setting inpaint_mode, default "glyph"):
  1. GLYPH-COMPLETE mask: dark connected components that touch the OCR box and are letter-sized are erased whole - the
     part of a letter outside the box goes too, and only the strokes are inpainted, not the whole rectangle.
     Components that are huge, long or reach the edge of the search area (the bubble outline, panel borders, artwork)
     are never selected. A box with no usable glyphs falls back to the plain rectangle ("box" mode = the old behaviour).
  2. Inside a verified WHITE bubble (utils/bubble_interior.py) text pixels the OCR missed entirely are erased too
     (lines 3 and 5 of a half-read bubble) - only letter-sized components, never the outline.
  3. In such a white bubble the erased pixels are painted with the bubble's own background colour instead of TELEA, so
     nothing is smeared and the outline is never pulled into the text area. TELEA is used only where there is no
     verified bubble (text over artwork, narration over a screentone).

The same pass reports, per bubble, the interior box and whether text was left unexplained (`missed_text`) - the editor
shows "check text" and the pipeline re-OCRs such bubbles (services/bubble_refine.py).
TODO: evaluate a generative inpainting model (e.g. LaMa) for text over artwork.
"""

import numpy as np
import cv2

from core.config import get_setting
from utils.bubble_interior import find_interior

MIN_WHITE_BRIGHTNESS = 200      # only "white" bubbles get the flat fill / extra erase; toned boxes use the OCR boxes + TELEA
LEFTOVER_MIN_PIXELS = 40        # dark pixels not covered by any OCR text ...
LEFTOVER_MIN_RATIO = 0.004      # ... and at least this share of the interior => "missed text"
GLYPH_DARK = 150                # gray level below which a pixel counts as ink when selecting glyph components
MIN_GLYPH_COVERAGE = 0.015      # a box whose glyph pixels are below this share of its area uses the rectangle instead
OUTLINE_BAND_PX = 21            # width (kernel size) of the band around a bubble interior where its outline is protected


def inpaint_mode() -> str:
    return "box" if get_setting("inpaint_mode", "glyph") == "box" else "glyph"


def _poly(box) -> np.ndarray:
    return np.array(box, np.int32).reshape((-1, 1, 2))


def _center(bbox):
    return (bbox[0] + bbox[2]) / 2.0, (bbox[1] + bbox[3]) / 2.0


def _bbox(box):
    xs, ys = [p[0] for p in box], [p[1] for p in box]
    return min(xs), min(ys), max(xs), max(ys)


def _gray(roi: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY) if roi.ndim == 3 else roi


# ------------------------------------------------------------------ glyph-complete mask of one text line

def glyph_mask(image: np.ndarray, box, with_ink: bool = False):
    """
    :return: ((x0, y0), mask_roi uint8) with the ink of the letters belonging to this OCR box, or None when no usable
             glyphs were found (the caller then uses the rectangle). with_ink=True adds a third item: the bool ROI of
             the selected letter strokes themselves (before the anti-aliasing halo).
    """
    ih, iw = image.shape[:2]
    bx1, by1, bx2, by2 = _bbox(box)
    bw, bh = max(1.0, bx2 - bx1), max(1.0, by2 - by1)
    ex, ey = max(4, int(0.3 * bh)), max(4, int(0.7 * bh))       # letters may stick out of the box (esp. vertically)
    x0, y0 = max(0, int(bx1) - ex), max(0, int(by1) - ey)
    x1, y1 = min(iw, int(bx2) + ex), min(ih, int(by2) + ey)
    if x1 - x0 < 3 or y1 - y0 < 3:
        return None
    gray = _gray(image[y0:y1, x0:x1])
    dark = (gray < GLYPH_DARK).astype(np.uint8)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(dark, connectivity=8)
    if n <= 1:
        return None

    box_roi = np.zeros(gray.shape, np.uint8)
    cv2.fillPoly(box_roi, [_poly(np.array(box, np.float32) - np.array([x0, y0], np.float32))], 255)
    box_roi = cv2.dilate(box_roi, np.ones((3, 3), np.uint8), iterations=1)
    touching = np.unique(labels[box_roi > 0])

    rh, rw = gray.shape
    min_area = max(10, int(0.004 * bw * bh))                    # screentone dots are far smaller than a letter stroke
    strong, small = [], []
    for lab in touching:
        if lab == 0:
            continue
        cx, cy, cw, ch, area = stats[lab]
        if cx <= 0 or cy <= 0 or cx + cw >= rw or cy + ch >= rh:
            continue                                            # reaches the edge of the area: outline / artwork
        if cw > max(10 * bh, 1.5 * bw) or ch > 2.5 * bh:
            continue                                            # too long / too tall for a letter or a word
        (strong if area >= min_area else small).append(lab)
    if not strong:
        return None
    ink = np.isin(labels, strong)
    if ink.sum() < MIN_GLYPH_COVERAGE * bw * bh:
        return None
    if len(small) > 25:
        small = []                                              # dozens of tiny specks = screentone texture, not punctuation
    if small:                                                   # periods, i-dots, commas: only when right next to a letter
        gap = max(3, int(0.3 * bh))
        near = cv2.dilate(ink.astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * gap + 1,) * 2)) > 0
        ink |= np.isin(labels, [lab for lab in small if near[labels == lab].mean() > 0.5])
    mask = cv2.dilate(ink.astype(np.uint8) * 255, np.ones((3, 3), np.uint8), iterations=2)   # anti-aliasing halo
    return ((x0, y0), mask, ink) if with_ink else ((x0, y0), mask)


# ------------------------------------------------------------------ one bubble

def _covered_mask(image, rx, ry, rh, rw, boxes, mode) -> np.ndarray:
    covered = np.zeros((rh, rw), np.uint8)
    for box in boxes:
        g = glyph_mask(image, box) if mode == "glyph" else None
        if g is not None:
            (gx, gy), gm = g
            ox, oy = gx - rx, gy - ry
            sy0, sx0 = max(0, oy), max(0, ox)
            sy1, sx1 = min(rh, oy + gm.shape[0]), min(rw, ox + gm.shape[1])
            if sy1 > sy0 and sx1 > sx0:
                covered[sy0:sy1, sx0:sx1] |= gm[sy0 - oy:sy1 - oy, sx0 - ox:sx1 - ox]
        shifted = np.array(box, np.float32) - np.array([rx, ry], np.float32)
        cv2.fillPoly(covered, [_poly(shifted)], 255)
    return cv2.dilate(covered, np.ones((5, 5), np.uint8), iterations=1)


def _letter_sized(leftover: np.ndarray, hull_h: int, hull_area: int) -> np.ndarray:
    """Keep only leftover components that look like text (not a big drawn element)."""
    n, labels, stats, _ = cv2.connectedComponentsWithStats(leftover.astype(np.uint8), connectivity=8)
    keep = [i for i in range(1, n) if stats[i][3] <= 0.45 * hull_h and stats[i][4] <= 0.04 * hull_area]
    return np.isin(labels, keep) if keep else np.zeros_like(leftover, dtype=bool)


def analyze_bubble(image: np.ndarray, b: dict, mode: str | None = None):
    """
    Interior check of ONE bubble: {"bubble_id", "bbox", "boxes"} (original coords).
    :return: (Interior | None, info, leftover) ; leftover = ROI-sized bool mask of letter-sized dark components inside
             the interior that no OCR text explains (None when there is no interior).
    """
    mode = mode or inpaint_mode()
    it = find_interior(image, _center(b["bbox"]), b["bbox"])
    if it is None:
        return None, {"interior_box": None, "missed_text": False, "extra_pixels": 0}, None

    rx, ry = it.origin
    rh, rw = it.hull.shape
    gray = _gray(image[ry:ry + rh, rx:rx + rw])
    covered = _covered_mask(image, rx, ry, rh, rw, b.get("boxes", []), mode)
    raw = (gray < 180) & (it.hull > 0) & (covered == 0)
    leftover = _letter_sized(raw, rh, it.area)
    n_left = int(leftover.sum())
    missed = n_left >= max(LEFTOVER_MIN_PIXELS, LEFTOVER_MIN_RATIO * it.area)
    return it, {"interior_box": list(it.bbox), "missed_text": bool(missed), "extra_pixels": n_left}, leftover


# ------------------------------------------------------------------ the whole page

def build_erase_mask(image: np.ndarray, boxes: list, bubbles: list | None = None, use_interior: bool = True,
                     mode: str | None = None):
    """
    :param image:   original BGR image.
    :param boxes:   polygons (original coordinates) of every text line to erase.
    :param bubbles: [{"bubble_id", "bbox": (x1,y1,x2,y2), "boxes": [polygons of THIS bubble's lines]}] (original coords)
                    - the bubbles whose interior should be analysed. None / [] = no interior pass.
    :param mode:    "glyph" | "box" (default: setting inpaint_mode).
    :return: (mask uint8 255 = erase, info, flats) ; info = {bubble_id: {"interior_box": [..] | None, "missed_text": bool,
             "extra_pixels": int}} ; flats = [(x0, y0, bool ROI mask, (b, g, r))] - regions to paint with a flat colour
             (verified white bubbles) instead of TELEA; inpaint() consumes them.
    """
    mode = mode or inpaint_mode()
    ih, iw = image.shape[:2]
    mask = np.zeros((ih, iw), np.uint8)
    rect = np.zeros((ih, iw), np.uint8)              # lines that fall back to the plain rectangle
    letters = np.zeros((ih, iw), bool)               # selected letter strokes: never "outline", see below
    for box in boxes:
        g = glyph_mask(image, box, with_ink=True) if mode == "glyph" else None
        if g is None:
            cv2.fillPoly(rect, [_poly(box)], 255)
        else:
            (gx, gy), gm, ink = g
            mask[gy:gy + gm.shape[0], gx:gx + gm.shape[1]] |= gm
            letters[gy:gy + ink.shape[0], gx:gx + ink.shape[1]] |= ink
    mask |= cv2.dilate(rect, np.ones((5, 5), np.uint8), iterations=1)

    info: dict = {}
    flats: list = []
    if not (use_interior and bubbles):
        return mask, info, flats
    letter_edge = cv2.dilate(letters.astype(np.uint8), np.ones((3, 3), np.uint8), iterations=1) > 0   # + anti-aliasing
    letter_halo = cv2.dilate(letters.astype(np.uint8), np.ones((3, 3), np.uint8), iterations=2)       # = glyph_mask halo

    for b in bubbles:
        it, bubble_info, leftover = analyze_bubble(image, b, mode)
        info[b["bubble_id"]] = bubble_info
        if it is None or it.brightness < MIN_WHITE_BRIGHTNESS:
            continue
        rx, ry = it.origin
        rh, rw = it.hull.shape
        roi_mask = mask[ry:ry + rh, rx:rx + rw]

        if bubble_info["missed_text"]:
            grown = cv2.dilate(leftover.astype(np.uint8) * 255, np.ones((5, 5), np.uint8), iterations=1)
            inside = cv2.dilate(it.hull, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)), iterations=1)
            roi_mask |= cv2.bitwise_and(grown, inside)

        # outline protection: a rectangle fallback or a glyph halo may have swallowed dark outline pixels outside the
        # interior. Only in a thin band around THIS bubble: the ROI is the whole find_interior search window (>= 480 px
        # each side, i.e. most of a manga page), and protecting every dark pixel outside this hull removed the text of
        # all OTHER bubbles from the mask - only the first white bubble of a page was erased, captions got a grey halo.
        # Selected letter strokes are never outline (glyph_mask already rejects components that look like one) - a
        # last line sitting close to the outline used to be "protected" and stayed on the page.
        gray = _gray(image[ry:ry + rh, rx:rx + rw])
        near_hull = cv2.dilate(it.hull, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)), iterations=1)
        outline_band = cv2.dilate(it.hull, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (OUTLINE_BAND_PX,) * 2), iterations=1)
        outline = (gray < 180) & (near_hull == 0) & (outline_band > 0) & ~letter_edge[ry:ry + rh, rx:rx + rw]
        roi_mask[outline & (cv2.bitwise_and(roi_mask, 255) > 0)] = 0

        # flat fill = the interior + this bubble's letters (with their halo) right next to the outline; TELEA there
        # mixed in the outline and left a grey ghost of the last line
        own_letters = (letter_halo[ry:ry + rh, rx:rx + rw] > 0) & (outline_band > 0)
        flat_region = (roi_mask > 0) & ((near_hull > 0) | own_letters)
        if flat_region.any():
            bright = gray[(it.hull > 0) & (gray >= 220)]
            color = tuple(int(c) for c in np.median(image[ry:ry + rh, rx:rx + rw][(it.hull > 0) & (gray >= 220)], axis=0)) \
                if bright.size else (255, 255, 255)
            flats.append((rx, ry, flat_region, color))
    return mask, info, flats


def inpaint(image: np.ndarray, mask: np.ndarray, flats: list | None = None) -> np.ndarray:
    """Paint the flat regions with their bubble colour, fill everything else that is masked from its surroundings (TELEA)."""
    out = image.copy()
    rest = mask.copy()
    for fx, fy, region, color in flats or []:
        h, w = region.shape
        out[fy:fy + h, fx:fx + w][region] = color
        rest[fy:fy + h, fx:fx + w][region] = 0
    if rest.any():
        out = cv2.inpaint(out, rest, 5, cv2.INPAINT_TELEA)
    return out


def erase_text_from_image(image_original, grouped_lines, to_original_coords, bubbles=None, use_interior=False):
    """Signature-compatible with the old helper (test_main.py): erase the given text lines."""
    boxes = [to_original_coords(line["box"]) for line in grouped_lines]
    mask, _, flats = build_erase_mask(image_original, boxes, bubbles, use_interior)
    return inpaint(image_original, mask, flats)
