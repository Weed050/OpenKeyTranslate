
# backend/utils/bubble_interior.py

"""
THE single bubble-interior detector (flood fill), shared by:
  * inpainting      - erase text pixels the OCR boxes missed, but only INSIDE a verified white bubble
                      (utils/inpainting.build_erase_mask)
  * typesetting     - the writable rectangle for the translated text (utils/typesetting.py)
  * bubble refine   - merge one bubble that OCR cut into pieces; re-OCR a bubble whose text is not fully detected
                      (services/bubble_refine.py)
Before, typesetting had its own copy of this algorithm and inpainting had none (it trusted OCR boxes only).

HOW IT WORKS
All dark pixels (text AND the bubble outline) are "walls"; walls are thickened a little so thin or broken outlines
are leak-proof; a flood fill from a seed near the text then covers the white interior. The filled area has holes where
the glyphs are - Interior.hull is the same area with the holes filled in (= "everything inside the outline").

Work is done in a window (ROI) around the reference box, so cost does not depend on the page size (a tall webtoon page
used to be converted to gray and dilated once PER BUBBLE). Guard rails reject the result (-> None, callers fall back
to OCR boxes) when the fill leaked out of the window, is far larger than the text, or is implausibly small.
"""

from dataclasses import dataclass

import cv2
import numpy as np


@dataclass
class Interior:
    bbox: tuple[int, int, int, int]      # x1, y1, x2, y2 of the filled area, IMAGE coordinates
    hull: np.ndarray                     # uint8 ROI-sized mask, 255 = inside the bubble outline (glyph holes included)
    origin: tuple[int, int]              # ROI top-left in image coordinates
    brightness: float                    # mean gray of the filled (free) pixels - white bubble ~ 240+
    area: int                            # number of hull pixels

    def contains(self, x: float, y: float) -> bool:
        ox, oy = self.origin
        ix, iy = int(x) - ox, int(y) - oy
        h, w = self.hull.shape
        return 0 <= ix < w and 0 <= iy < h and self.hull[iy, ix] > 0


def nearest_free_pixel(walls: np.ndarray, cx: float, cy: float, max_radius: int = 60, step: int = 5):
    """Nearest pixel that is not a wall (the seed is often ON a glyph stroke). None if there is none within max_radius."""
    h, w = walls.shape
    cx, cy = int(cx), int(cy)
    if 0 <= cy < h and 0 <= cx < w and walls[cy, cx] == 0:
        return cx, cy
    for r in range(step, max_radius, step):
        # all 8 neighbours on the ring (the old copy listed (r, r) twice and never tried (r, -r))
        for dx, dy in ((-r, 0), (r, 0), (0, -r), (0, r), (-r, -r), (r, r), (-r, r), (r, -r)):
            nx, ny = cx + dx, cy + dy
            if 0 <= nx < w and 0 <= ny < h and walls[ny, nx] == 0:
                return nx, ny
    return None


# WHAT REJECTS A FILL (a rejection makes every caller fall back to plain OCR boxes - it is never an error):
#   * LEAK      the fill reached the edge of the search window  -> open outline / no outline (text on artwork or on bare paper)
#   * PAGE      the interior is a large share of the page      -> that is the page background, not a bubble
#   * RAGGED    low solidity (area / convex hull)               -> leaked through a gap into a corridor
#   * TRAPPED   smaller than the text itself                    -> the seed fell into a closed pocket of a letter
# There is deliberately NO "interior may be at most N x the text" rule: a bubble holding two short words is easily 6-10x
# its text. That rule (2.5, then 6.0) rejected exactly the large bubbles with little text - the ones where OCR most
# often misses part of the text - and every feature built on the interior silently did nothing for them.
WINDOW_FACTOR = 6.0             # search window = text box +- this many text sizes ...
MIN_WINDOW_PAD = 480            # ... but at least this many pixels each side (a two-lobed bubble can be 500+ px tall)
MAX_PAGE_SHARE = 0.45
MIN_SOLIDITY = 0.72


def _seed_candidates(bbox, text_center):
    """Where to start the fill, best first: the text centre often lies ON a glyph or in a closed letter pocket, so also
    try the white margins just outside the text box (still inside the bubble in the common case)."""
    x1, y1, x2, y2 = bbox
    w, h = max(1, x2 - x1), max(1, y2 - y1)
    cx, cy = text_center
    gap = max(4, int(0.25 * h))
    pts = [(cx, cy), (cx, y1 - gap), (cx, y2 + gap), (x1 - gap, cy), (x2 + gap, cy),
           (x1 + 0.25 * w, y1 - gap), (x1 + 0.75 * w, y2 + gap), (x1 + 0.25 * w, y2 + gap), (x1 + 0.75 * w, y1 - gap)]
    return pts


def find_interior(image_bgr: np.ndarray, seed_point, ref_bbox, dark_thresh: int = 180, border_dilate: int = 2,
                  max_growth: float | None = None, min_growth: float = 0.9, why: list | None = None) -> Interior | None:
    """
    :param seed_point: (x, y) near the text (centre of the OCR box).
    :param ref_bbox:   (x1, y1, x2, y2) of the text the interior must contain.
    :param max_growth: optional upper limit on interior size / text size. None (default) = no such limit.
    :param why:        optional list; a human-readable rejection reason is appended (for logs / debugging).
    """
    def reject(msg):
        if why is not None:
            why.append(msg)
        return None

    ih, iw = image_bgr.shape[:2]
    x1, y1, x2, y2 = (int(round(v)) for v in ref_bbox)
    bw, bh = max(1, x2 - x1), max(1, y2 - y1)
    padx = max(MIN_WINDOW_PAD, int(bw * (WINDOW_FACTOR - 1))) + 8
    pady = max(MIN_WINDOW_PAD, int(bh * (WINDOW_FACTOR - 1))) + 8
    rx1, ry1, rx2, ry2 = max(0, x1 - padx), max(0, y1 - pady), min(iw, x2 + padx), min(ih, y2 + pady)
    if rx2 - rx1 < 4 or ry2 - ry1 < 4:
        return reject("empty window")

    roi = image_bgr[ry1:ry2, rx1:rx2]
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY) if roi.ndim == 3 else roi
    dark = (gray < dark_thresh).astype(np.uint8) * 255
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (border_dilate * 2 + 1,) * 2)
    walls = cv2.dilate(dark, kernel, iterations=1)
    rh, rw = gray.shape
    barrier = np.zeros((rh + 2, rw + 2), np.uint8)
    barrier[1:-1, 1:-1] = (walls > 0).astype(np.uint8)
    text_c = (seed_point[0] - rx1, seed_point[1] - ry1)

    tried = np.zeros((rh, rw), bool)               # pockets already filled and found useless: do not seed in them again
    last_reason = "no free pixel near the seed (text sits on the outline / artwork)"
    for sx, sy in _seed_candidates((x1 - rx1, y1 - ry1, x2 - rx1, y2 - ry1), text_c):
        seed = nearest_free_pixel(walls, sx, sy)
        if seed is None or tried[seed[1], seed[0]]:
            continue
        filled = np.zeros_like(gray)
        cv2.floodFill(filled, barrier.copy(), seed, 255)
        region = filled == 255
        tried |= region

        if region[0, :].any() or region[-1, :].any() or region[:, 0].any() or region[:, -1].any():
            last_reason = "fill leaked out of the search window (open outline or no outline)"
            continue
        contours, _ = cv2.findContours(region.astype(np.uint8) * 255, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            continue
        largest = max(contours, key=cv2.contourArea)
        x, y, w, h = cv2.boundingRect(largest)
        if w < bw * min_growth or h < bh * min_growth:
            last_reason = f"interior {w}x{h}px is smaller than the text ({bw}x{bh}px) - trapped in a pocket"
            continue
        if cv2.pointPolygonTest(largest, (float(text_c[0]), float(text_c[1])), False) < 0:
            last_reason = "the filled area does not contain the text (seed was outside the bubble)"
            continue
        if max_growth is not None and (w > bw * max_growth or h > bh * max_growth):
            return reject(f"interior {w}x{h}px is more than {max_growth}x the text ({bw}x{bh}px)")
        if w * h > MAX_PAGE_SHARE * iw * ih:
            return reject("interior covers most of the page (page background, not a bubble)")
        hull_area = cv2.contourArea(cv2.convexHull(largest))
        solidity = cv2.contourArea(largest) / hull_area if hull_area > 0 else 0.0
        if solidity < MIN_SOLIDITY:
            last_reason = f"interior is ragged (solidity {solidity:.2f}) - probably leaked through a gap"
            continue

        hull = np.zeros_like(gray)
        cv2.drawContours(hull, [largest], -1, 255, thickness=-1)   # filled polygon: glyph holes become part of it
        return Interior(
            bbox=(rx1 + x, ry1 + y, rx1 + x + w, ry1 + y + h), hull=hull, origin=(rx1, ry1),
            brightness=float(gray[region].mean()), area=int((hull > 0).sum()),
        )
    return reject(last_reason)


def detect_bubble_interior(image_bgr, seed_point, ocr_bbox, dark_thresh=180, border_dilate=2, max_growth=None):
    """
    Backward-compatible entry point used by typesetting: returns (hull mask, bbox) or (None, None).
    The rejection reason is printed, as the old per-module copy did.
    """
    why: list = []
    it = find_interior(image_bgr, seed_point, ocr_bbox, dark_thresh, border_dilate, max_growth, why=why)
    if it is None:
        print(f"[BUBBLE GUARD] {why[0] if why else 'rejected'} - falling back to the OCR bounding box.")
        return None, None
    return it.hull, it.bbox
