
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
from utils.ocr_rescue import is_tilted, poly_angle

MIN_WHITE_BRIGHTNESS = 200      # only "white" bubbles get the flat fill / extra erase; toned boxes use the OCR boxes + TELEA
LEFTOVER_MIN_PIXELS = 40        # dark pixels not covered by any OCR text ...
LEFTOVER_MIN_RATIO = 0.004      # ... and at least this share of the interior => "missed text"
GLYPH_DARK = 150                # gray level below which a pixel counts as ink when selecting glyph components
MIN_GLYPH_COVERAGE = 0.015      # a box whose glyph pixels are below this share of its area uses the rectangle instead
OUTLINE_BAND_PX = 21            # width (kernel size) of the band around a bubble interior where its outline is protected
GLYPH_FILL_HOLES = True         # also erase the closed inside of letters (fill of outlined lettering), see _letter_holes
GHOST_REACH_PX = 5              # light pixels this close to erased text are repainted flat (JPEG halo of the letters)
GHOST_MIN_GRAY = 200            # ... only when at least this light: outline / artwork pixels are never repainted
PAPER_MIN_GRAY = 200            # text outside a closed bubble: "paper" = pixels at least this light ...
PAPER_MIN_SHARE = 0.6           # ... covering this share of the surroundings, else TELEA as before
PAPER_MAX_SPECKS = 0.012        # ... and at most this share of it in tiny dark dots (paper <= 0.004, light screentone 0.02-0.07)
SPECK_MAX_AREA = 12             # a dark piece this small (px) is a screentone dot, not line art
ART_GAP_PX = 3                  # paper fill stops this far from non-text ink; TELEA blends the last pixels
CLEANUP_PLAIN_STD = 12          # a manual cleanup area whose border varies less than this is painted flat
CLEANUP_MARK_DELTA = 30         # "Clean area": a pixel this much lighter / darker than the area's background is a mark
RIM_LIGHT_GRAY = 200            # stroked lettering (see _light_rim): a rim pixel is at least this light ...
RIM_RING_SHARE = 0.8            # ... a ring around the letters belongs to the rim when this share of it is light ...
RIM_GROUND_MAX = 0.35           # ... and the ground just beyond the rim is at most this share light
RIM_MAX_SHARE = 0.25            # widest rim accepted, as a share of the line height


def min_glyph_area(bw: float, bh: float) -> int:
    """
    Smallest connected ink piece that counts as a LETTER (smaller ones are dots / commas / screentone).
    From the line HEIGHT only (0.3 x: still above screentone dots, at or below the dots of "...."). It used to be 0.4 % of the whole box area,
    which grows with the line LENGTH - in a long caption of small letters (page_001: 550 x 12 px) every thin letter fell
    under it, there were more than 25 such "specks", they were taken for screentone and none of them was erased.
    """
    return max(6, int(0.3 * bh))


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
    A tilted box (diagonal map label) is measured in a levelled copy: its axis-aligned bounds are many letters tall,
    which would let map lines and artwork pass the letter-size checks below.
    """
    if len(box) == 4 and is_tilted(poly_angle(box)):
        return _tilted_glyph_mask(image, box, with_ink)
    return _level_glyph_mask(image, box, with_ink)


def _tilted_glyph_mask(image: np.ndarray, box, with_ink: bool = False):
    """glyph_mask of a tilted box: rotate the surroundings level, measure there, rotate the masks back."""
    ih, iw = image.shape[:2]
    pts = np.array(box, np.float32)
    bx1, by1, bx2, by2 = _bbox(box)
    pad = int(max(bx2 - bx1, by2 - by1) * 0.5) + 8
    rx0, ry0, rx1, ry1 = max(0, int(bx1) - pad), max(0, int(by1) - pad), min(iw, int(bx2) + pad), min(ih, int(by2) + pad)
    crop = image[ry0:ry1, rx0:rx1]
    h, w = crop.shape[:2]
    center = tuple(float(v) for v in pts.mean(axis=0) - np.array([rx0, ry0], np.float32))
    rot = cv2.getRotationMatrix2D(center, poly_angle(box), 1.0)            # turns the text line level
    fill = (255,) * (crop.shape[2] if crop.ndim == 3 else 1)
    level = cv2.warpAffine(crop, rot, (w, h), flags=cv2.INTER_LINEAR, borderValue=fill)
    local = pts - np.array([rx0, ry0], np.float32)
    level_box = cv2.transform(local.reshape(-1, 1, 2), rot).reshape(-1, 2).tolist()
    g = _level_glyph_mask(level, level_box, with_ink, centre_inside=True)
    if g is None:
        return None
    (gx, gy), gm = g[0], g[1]
    back = cv2.invertAffineTransform(rot)

    def unturn(roi, dtype):
        full = np.zeros((h, w), np.uint8)
        full[gy:gy + roi.shape[0], gx:gx + roi.shape[1]] = roi.astype(np.uint8) * (1 if dtype is np.uint8 else 255)
        return cv2.warpAffine(full, back, (w, h), flags=cv2.INTER_NEAREST, borderValue=0)

    mask = unturn(gm, np.uint8)
    ys, xs = np.nonzero(mask)
    if not len(ys):
        return None
    y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
    out = ((rx0 + int(x0), ry0 + int(y0)), mask[y0:y1, x0:x1])
    if with_ink:
        out += (unturn(g[2], bool)[y0:y1, x0:x1] > 0,)
    return out


def _level_glyph_mask(image: np.ndarray, box, with_ink: bool = False, centre_inside: bool = False):
    """centre_inside: only pieces centred inside the box (a map label crossing artwork: the art only touches it)."""
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
    min_area = min_glyph_area(bw, bh)                           # screentone dots are far smaller than a letter stroke
    strong, small = [], []
    for lab in touching:
        if lab == 0:
            continue
        cx, cy, cw, ch, area = stats[lab]
        if cx <= 0 or cy <= 0 or cx + cw >= rw or cy + ch >= rh:
            continue                                            # reaches the edge of the area: outline / artwork
        if cw > max(10 * bh, 1.5 * bw) or ch > 2.5 * bh:
            continue                                            # too long / too tall for a letter or a word
        if centre_inside and not (bx1 - x0 <= cx + cw / 2 <= bx2 - x0 and by1 - y0 <= cy + ch / 2 <= by2 - y0):
            continue
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
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * gap + 1,) * 2)
        for _ in range(6):                                      # a chain: the dots of "...." sit next to each other,
            near = cv2.dilate(ink.astype(np.uint8), kernel) > 0     # only the first one next to the letter
            added = [lab for lab in small if near[labels == lab].mean() > 0.5]
            if not added:
                break
            ink |= np.isin(labels, added)
            small = [lab for lab in small if lab not in added]
    erase = ink | _letter_holes(ink, 1.5 * bh * bh) if GLYPH_FILL_HOLES else ink
    rim = _light_rim(gray, ink, bh)
    if rim is not None:
        erase = erase | rim
    mask = cv2.dilate(erase.astype(np.uint8) * 255, np.ones((3, 3), np.uint8), iterations=2)   # anti-aliasing halo
    return ((x0, y0), mask, ink) if with_ink else ((x0, y0), mask)


def _light_rim(gray: np.ndarray, ink: np.ndarray, bh: float):
    """
    The light rim of STROKED lettering on a darker ground: dark letters with a white outline (titles, captions, sound
    effects over artwork or colour pages). Only the dark letters are ink, so the rim stayed and TELEA spread it into the
    erased letters - white blobs where the text was ("Night of the Intern" on a yellow floor, Give My Regards to Black
    Jack, Chapter_1/page_004). Rim = the rings around the letters that are light all round, followed by a ground that
    is not. Text in a white bubble or on paper is light far beyond RIM_MAX_SHARE of a line height and never gets one.
    :return: bool ROI of the rim (+ 2 px of its edge towards the ground), or None.
    """
    reach = max(3, int(RIM_MAX_SHARE * bh))
    dist = cv2.distanceTransform((~ink).astype(np.uint8), cv2.DIST_L2, 3)      # distance to the nearest letter pixel
    light = gray >= RIM_LIGHT_GRAY
    width = 1                                        # ring 1 = the letters' own grey anti-aliasing, not judged
    for d in range(2, reach + 1):
        ring = (dist > d - 1) & (dist <= d)
        if ring.sum() < 10 or light[ring].mean() < RIM_RING_SHARE:
            break
        width = d
    else:
        return None                                  # light all the way out: a bubble / paper, not a rim
    if width < 2:
        return None
    ground = (dist > width + 1) & (dist <= width + 4)
    if ground.sum() < 10 or light[ground].mean() > RIM_GROUND_MAX:
        return None
    return (dist <= width + 2) & ~ink


def _letter_holes(ink: np.ndarray, max_area: float) -> np.ndarray:
    """
    Closed areas INSIDE the selected letters: the light fill of OUTLINED lettering (dark outline, yellow / white inside -
    only the outline is dark enough to be ink, so the fill used to stay on the page as a pale ghost letter), and the
    counters of O / A / 8. = free pixels that cannot reach the edge of the area without crossing ink, each at most
    max_area (about one letter) so a large enclosed region is never taken.
    """
    n, labels, stats, _ = cv2.connectedComponentsWithStats((~ink).astype(np.uint8), connectivity=4)
    if n <= 2:
        return np.zeros_like(ink)
    border = set(np.unique(np.concatenate([labels[0], labels[-1], labels[:, 0], labels[:, -1]])).tolist())
    keep = [i for i in range(1, n) if i not in border and stats[i][4] <= max_area]
    return np.isin(labels, keep) if keep else np.zeros_like(ink)


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

def _plain_ground_flat(image: np.ndarray, mask: np.ndarray, b: dict):
    """
    Text that is NOT in a closed white bubble but on plain light paper (captions between panels, narration boxes with
    an open edge): (x0, y0, region, colour) to paint with the paper colour, or None. TELEA there pulled the colour of the
    nearby artwork into the hole - the grey smudge under "WITH THAT KIND OF..." on Chapter_1/page_034. Only masked pixels
    farther than ART_GAP_PX from any non-text ink are painted (TELEA still blends the edge next to the artwork), plus the
    light JPEG halo of the letters; a region whose surroundings are not mostly paper (text over screentone / art) is left
    to TELEA as before.
    """
    ih, iw = image.shape[:2]
    boxes = b.get("boxes", [])
    heights = [_text_height(p) for p in boxes] or [b["bbox"][3] - b["bbox"][1]]
    m = max(6, int(sorted(heights)[len(heights) // 2]))
    x1, y1 = max(0, int(b["bbox"][0]) - m), max(0, int(b["bbox"][1]) - m)
    x2, y2 = min(iw, int(b["bbox"][2]) + m), min(ih, int(b["bbox"][3]) + m)
    if x2 - x1 < 4 or y2 - y1 < 4:
        return None
    roi_mask = mask[y1:y2, x1:x2] > 0
    if not roi_mask.any():
        return None
    gray = _gray(image[y1:y2, x1:x2])
    free = ~roi_mask
    if any(len(p) == 4 and is_tilted(poly_angle(p)) for p in boxes):
        # a diagonal label's axis-aligned square is mostly whatever lies beside the slant: judge the band along it
        zone = np.zeros(free.shape, np.uint8)
        for p in boxes:
            cv2.fillPoly(zone, [_poly(np.array(p, np.float32) - np.array([x1, y1], np.float32))], 255)
        free &= cv2.dilate(zone, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * m + 1,) * 2)) > 0
    paper = free & (gray >= PAPER_MIN_GRAY)
    if paper.sum() < PAPER_MIN_SHARE * max(1, free.sum()):
        return None
    if _speck_share(gray, free) > PAPER_MAX_SPECKS:
        return None                                    # light screentone (shaded area): mostly light, but not paper
    color = tuple(int(c) for c in np.median(image[y1:y2, x1:x2][paper], axis=0))
    art = ((gray < 180) & free).astype(np.uint8)
    far_from_art = cv2.distanceTransform((1 - art).astype(np.uint8), cv2.DIST_L2, 3) > ART_GAP_PX
    near_text = cv2.dilate(roi_mask.astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * GHOST_REACH_PX + 1,) * 2)) > 0
    ghost = near_text & (gray >= GHOST_MIN_GRAY)
    region = (roi_mask | ghost) & far_from_art
    return (x1, y1, region, color) if region.any() else None


def _text_height(box) -> float:
    """Letter height of an OCR box: across the slant for a tilted one (its axis-aligned height is mostly not text)."""
    if len(box) == 4 and is_tilted(poly_angle(box)):
        (_, _), (x1, y1), (x2, y2), _ = box
        return float(np.hypot(x2 - x1, y2 - y1))
    return _bbox(box)[3] - _bbox(box)[1]


def _speck_share(gray: np.ndarray, area: np.ndarray) -> float:
    """Share of `area` covered by tiny dark dots - the dots of screentone; paper has none, line art makes big pieces."""
    dark = (area & (gray < PAPER_MIN_GRAY)).astype(np.uint8)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(dark, connectivity=8)
    tiny = [i for i in range(1, n) if stats[i][4] <= SPECK_MAX_AREA]
    return float(np.isin(labels, tiny).sum()) / max(1, int(area.sum())) if tiny else 0.0


CLEANUP_MODES = ("clean", "fill", "redraw")
CLEANUP_SHAPES = ("rect", "ellipse")


class Redraw:
    """A cleanup area for the AI redraw (services/lama_service.py), done by inpaint() after everything else."""

    def __init__(self, box, shape: np.ndarray | None = None):
        self.box = box
        self.shape = shape      # bool mask of the box (an ellipse) or None = the whole rectangle


def _shape_mask(h: int, w: int, shape: str | None) -> np.ndarray:
    """Bool mask of an h x w box: all True for a rectangle, the inscribed ellipse for "ellipse"."""
    if shape != "ellipse":
        return np.ones((h, w), bool)
    m = np.zeros((h, w), np.uint8)
    cv2.ellipse(m, (((w - 1) / 2.0, (h - 1) / 2.0), (float(w), float(h)), 0.0), 255, -1)    # RotatedRect form
    return m > 0


def _shape_border(image: np.ndarray, x1: int, y1: int, inside: np.ndarray, r: int = 3):
    """(BGR pixels of the r px ring just outside the shape `inside` at x1, y1, is that ring one plain colour)."""
    ih, iw = image.shape[:2]
    h, w = inside.shape
    ox1, oy1, ox2, oy2 = max(0, x1 - r), max(0, y1 - r), min(iw, x1 + w + r), min(ih, y1 + h + r)
    big = np.zeros((oy2 - oy1, ox2 - ox1), np.uint8)
    big[y1 - oy1:y1 - oy1 + h, x1 - ox1:x1 - ox1 + w] = inside
    ring = (cv2.dilate(big, np.ones((2 * r + 1, 2 * r + 1), np.uint8)) > 0) & (big == 0)
    patch = image[oy1:oy2, ox1:ox2]
    border = patch[ring]
    plain = bool(border.size) and float(_gray(patch)[ring].std()) <= CLEANUP_PLAIN_STD
    return border, plain


def _add_cleanups(image: np.ndarray, mask: np.ndarray, flats: list, cleanups) -> None:
    """
    Areas the user marked with the editor's clean tools (page JSON "cleanups": [{"box": [x1, y1, x2, y2], "mode"?,
    "shape"?}]): leftover traces the automatic pass did not catch. Modes:
      * "clean" (default, also entries without a mode): a rectangle whose border is one plain colour is painted with it
        whole (as before - nothing crosses its edge). Otherwise only the MARKS inside it go (_clean_marks); lines and
        artwork crossing its edge stay. Before 2026-11-08 such a rectangle was TELEA-filled whole: one touching a box
        border erased the border and smeared it with the artwork into a dark blot.
      * "fill": the whole rectangle in the most common colour of its border ("paint over with the background").
      * "redraw": the whole rectangle redrawn by the AI model, when installed (inpaint() does it last); without the
        model it is cleaned like "clean".
    shape "ellipse" (since 2026-11-08; absent = rectangle): only the ellipse inscribed in the box is touched, and its
    colour comes from the ring just outside the ELLIPSE - in an oval or spiky bubble the corners of a rectangle reach
    the outline (or the art beyond it), so a rectangle that is big enough for the text paints over them.
    """
    ih, iw = image.shape[:2]
    for c in cleanups or []:
        try:
            x1, y1, x2, y2 = (int(round(float(v))) for v in c["box"])
        except (KeyError, TypeError, ValueError):
            continue
        x1, x2 = sorted((max(0, x1), min(iw, x2)))
        y1, y2 = sorted((max(0, y1), min(ih, y2)))
        if x2 - x1 < 2 or y2 - y1 < 2:
            continue
        mode = c.get("mode") if c.get("mode") in CLEANUP_MODES else "clean"
        ellipse = c.get("shape") == "ellipse"
        inside = _shape_mask(y2 - y1, x2 - x1, "ellipse" if ellipse else "rect")
        if mode == "redraw":
            from services.lama_service import installed
            if installed():
                flats.append(Redraw((x1, y1, x2, y2), inside if ellipse else None))
                continue
            mode = "clean"
        border, plain = (_shape_border(image, x1, y1, inside) if ellipse
                         else _cleanup_border(image, x1, y1, x2, y2))
        if mode == "fill":
            colour = _common_colour(border) if border.size else (255, 255, 255)
            flats.append((x1, y1, inside.copy(), colour))
            continue
        if plain:                                       # one plain colour all round: nothing crosses the edge - paint it all
            flats.append((x1, y1, inside.copy(), tuple(int(v) for v in np.median(border, axis=0))))
            continue
        marks = _clean_marks(image, x1, y1, x2, y2)
        if marks is None:                               # nothing mark-like found: fill it from the surroundings as before
            mask[y1:y2, x1:x2][inside] = 255
            continue
        erase, colour = marks
        erase = erase & inside
        if colour is not None:
            flats.append((x1, y1, erase, colour))
        else:
            mask[y1:y2, x1:x2][erase] = 255


def _cleanup_border(image: np.ndarray, x1: int, y1: int, x2: int, y2: int, r: int = 3):
    """(BGR pixels of the r px ring around the rectangle, is that ring one plain colour)."""
    ih, iw = image.shape[:2]
    ox1, oy1, ox2, oy2 = max(0, x1 - r), max(0, y1 - r), min(iw, x2 + r), min(ih, y2 + r)
    ring = np.ones((oy2 - oy1, ox2 - ox1), bool)
    ring[y1 - oy1:y2 - oy1, x1 - ox1:x2 - ox1] = False
    border = image[oy1:oy2, ox1:ox2][ring]
    plain = bool(border.size) and float(_gray(image[oy1:oy2, ox1:ox2])[ring].std()) <= CLEANUP_PLAIN_STD
    return border, plain


def _common_colour(pixels: np.ndarray) -> tuple:
    """Most common colour (16-level bins, then the median inside the winning bin) - the background, not a mix of it with lines."""
    px = pixels.reshape(-1, pixels.shape[-1]) if pixels.ndim > 1 else pixels.reshape(-1, 1)
    keys = (px // 16).astype(np.int32)
    flat = keys[:, 0] * 256 + (keys[:, 1] * 16 + keys[:, 2] if px.shape[1] == 3 else 0)
    vals, counts = np.unique(flat, return_counts=True)
    win = px[flat == vals[np.argmax(counts)]]
    return tuple(int(v) for v in np.median(win, axis=0))


def _clean_marks(image: np.ndarray, x1: int, y1: int, x2: int, y2: int):
    """
    "Clean area" without collateral damage: the marks to erase inside the rectangle = pieces that differ from its
    background (darker or lighter) and lie entirely INSIDE it (letter remains, ghosts, specks). Kept: straight lines entering from outside (box and
    panel borders - a letter glued to such a border is cut off it and erased) and anything else crossing the edge
    (artwork). The marks are painted with the plain colour around them, or TELEA-filled on their own pixels when the
    ground there is not plain - never the whole rectangle.
    :return: (bool ROI of the pixels to erase, BGR colour or None = TELEA), or None when no mark was found.
    """
    roi = image[y1:y2, x1:x2]
    gray = _gray(roi).astype(np.int16)
    h, w = gray.shape
    if h < 4 or w < 4:
        return None
    border, _ = _cleanup_border(image, x1, y1, x2, y2)
    border_gray = _gray(border.reshape(-1, 1, 3)) if border.ndim == 2 and border.shape[1] == 3 else border
    bg = int(np.median(border_gray)) if border.size else 255
    # a mark differs from the background in EITHER direction: dark letters / pale ghosts (gray < 225) on a white box,
    # white letters on a black caption box (Nihonkoku Shoukan narration), both on a grey tone
    dark = (np.abs(gray - bg) > CLEANUP_MARK_DELTA).astype(np.uint8)
    if not dark.any():
        return None
    # straight lines that reach the edge of the rectangle (borders of boxes / panels): never erased
    lv, lh = max(9, h // 2), max(9, w // 2)
    lines = cv2.morphologyEx(dark, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, lv))) \
        | cv2.morphologyEx(dark, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (lh, 1)))
    if lines.any():
        n, labels, stats, _ = cv2.connectedComponentsWithStats(lines, connectivity=8)
        edge = [i for i in range(1, n) if stats[i][0] <= 0 or stats[i][1] <= 0
                or stats[i][0] + stats[i][2] >= w or stats[i][1] + stats[i][3] >= h]
        lines = np.isin(labels, edge).astype(np.uint8)
    keep = cv2.dilate(lines, np.ones((3, 3), np.uint8), iterations=1) > 0
    marks = (dark > 0) & ~keep
    n, labels, stats, _ = cv2.connectedComponentsWithStats(marks.astype(np.uint8), connectivity=8)
    inside = [i for i in range(1, n) if stats[i][0] > 0 and stats[i][1] > 0
              and stats[i][0] + stats[i][2] < w and stats[i][1] + stats[i][3] < h]
    if not inside:
        return None
    sel = np.isin(labels, inside)
    sel |= _letter_holes(sel, 0.25 * h * w)           # the inside of outlined letters / counters
    erase = cv2.dilate(sel.astype(np.uint8), np.ones((3, 3), np.uint8), iterations=2) > 0    # anti-aliasing halo
    erase &= ~(lines > 0)
    # the ground right around the marks decides: one plain colour -> paint it, else TELEA on the marks only
    ring = (cv2.dilate(erase.astype(np.uint8), np.ones((5, 5), np.uint8), iterations=1) > 0) & ~erase & ~(dark > 0)
    ground = roi[ring]
    if ground.size and float(gray[ring].std()) <= CLEANUP_PLAIN_STD:
        return erase, tuple(int(v) for v in np.median(ground, axis=0))
    return erase, None


def build_erase_mask(image: np.ndarray, boxes: list, bubbles: list | None = None, use_interior: bool = True,
                     mode: str | None = None, cleanups: list | None = None):
    """
    :param image:   original BGR image.
    :param boxes:   polygons (original coordinates) of every text line to erase.
    :param bubbles: [{"bubble_id", "bbox": (x1,y1,x2,y2), "boxes": [polygons of THIS bubble's lines]}] (original coords)
                    - the bubbles whose interior should be analysed. None / [] = no interior pass.
    :param mode:    "glyph" | "box" (default: setting inpaint_mode).
    :param cleanups: the page's manual "Clean area" rectangles (see _add_cleanups); applied in every mode.
    :return: (mask uint8 255 = erase, info, flats) ; info = {bubble_id: {"interior_box": [..] | None, "missed_text": bool,
             "extra_pixels": int}} ; flats = [(x0, y0, bool ROI mask, (b, g, r))] - regions to paint with a flat colour
             (verified white bubbles, text on plain paper, plain cleanup areas) instead of TELEA; inpaint() consumes them.
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
        _add_cleanups(image, mask, flats, cleanups)
        return mask, info, flats
    letter_edge = cv2.dilate(letters.astype(np.uint8), np.ones((3, 3), np.uint8), iterations=1) > 0   # + anti-aliasing
    letter_halo = cv2.dilate(letters.astype(np.uint8), np.ones((3, 3), np.uint8), iterations=2)       # = glyph_mask halo

    for b in bubbles:
        it, bubble_info, leftover = analyze_bubble(image, b, mode)
        info[b["bubble_id"]] = bubble_info
        if it is None or it.brightness < MIN_WHITE_BRIGHTNESS:
            plain = _plain_ground_flat(image, mask, b)       # no closed white bubble: text on plain paper?
            if plain is not None:
                flats.append(plain)
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
        # JPEG halo of the letters: compression ringing reaches a few px beyond the ink, too light for the glyph mask
        # (gray 240-252) but a visible pale ghost of the text on the flat white (Chapter_1/page_034). Near the erased
        # text every LIGHT interior pixel gets the bubble colour as well - dark pixels (outline, drawings) never do.
        near_text = cv2.dilate(roi_mask, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * GHOST_REACH_PX + 1,) * 2)) > 0
        flat_region |= (it.hull > 0) & near_text & (gray >= GHOST_MIN_GRAY)
        if flat_region.any():
            bright = gray[(it.hull > 0) & (gray >= 220)]
            color = tuple(int(c) for c in np.median(image[ry:ry + rh, rx:rx + rw][(it.hull > 0) & (gray >= 220)], axis=0)) \
                if bright.size else (255, 255, 255)
            flats.append((rx, ry, flat_region, color))
    _add_cleanups(image, mask, flats, cleanups)
    return mask, info, flats


def inpaint(image: np.ndarray, mask: np.ndarray, flats: list | None = None) -> np.ndarray:
    """
    Paint the flat regions with their bubble colour, fill everything else that is masked from its surroundings (TELEA),
    then the user's "Redraw (AI)" rectangles (Redraw items in `flats`) - last, so the model sees the cleaned page.
    """
    out = image.copy()
    rest = mask.copy()
    redraws = []
    for item in flats or []:
        if isinstance(item, Redraw):
            redraws.append(item)
            continue
        fx, fy, region, color = item
        h, w = region.shape
        out[fy:fy + h, fx:fx + w][region] = color
        rest[fy:fy + h, fx:fx + w][region] = 0
    if rest.any():
        out = cv2.inpaint(out, rest, 5, cv2.INPAINT_TELEA)
    if redraws:
        from services.lama_service import redraw
        for r in redraws:
            try:
                out = redraw(out, r.box, r.shape)
            except Exception as e:                      # noqa: BLE001 - a failed redraw leaves the area as cleaned so far
                print(f"[INPAINT] AI redraw of {r.box} failed: {e}")
    return out


def erase_text_from_image(image_original, grouped_lines, to_original_coords, bubbles=None, use_interior=False):
    """Signature-compatible with the old helper (test_main.py): erase the given text lines."""
    boxes = [to_original_coords(line["box"]) for line in grouped_lines]
    mask, _, flats = build_erase_mask(image_original, boxes, bubbles, use_interior)
    return inpaint(image_original, mask, flats)
