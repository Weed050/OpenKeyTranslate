
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


def find_interior(image_bgr: np.ndarray, seed_point, ref_bbox, dark_thresh: int = 180, border_dilate: int = 2,
                  max_growth: float = 2.5, min_growth: float = 0.9, why: list | None = None) -> Interior | None:
    """
    :param seed_point: (x, y) near the text (centre of the OCR box). Moved to the nearest free pixel.
    :param ref_bbox:   (x1, y1, x2, y2) of the text the interior must contain; limits are relative to it.
    :param max_growth: the interior may be at most this many times wider / taller than ref_bbox.
    :param why:        optional list; a human-readable rejection reason is appended (for logs / debugging).
    """
    def reject(msg):
        if why is not None:
            why.append(msg)
        return None

    ih, iw = image_bgr.shape[:2]
    x1, y1, x2, y2 = (int(round(v)) for v in ref_bbox)
    bw, bh = max(1, x2 - x1), max(1, y2 - y1)
    padx = int(bw * (max_growth - 1)) + 8          # the interior may stick out of the text box by (growth-1) * size
    pady = int(bh * (max_growth - 1)) + 8
    rx1, ry1, rx2, ry2 = max(0, x1 - padx), max(0, y1 - pady), min(iw, x2 + padx), min(ih, y2 + pady)
    if rx2 - rx1 < 4 or ry2 - ry1 < 4:
        return reject("empty window")

    roi = image_bgr[ry1:ry2, rx1:rx2]
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY) if roi.ndim == 3 else roi
    dark = (gray < dark_thresh).astype(np.uint8) * 255
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (border_dilate * 2 + 1,) * 2)
    walls = cv2.dilate(dark, kernel, iterations=1)

    seed = nearest_free_pixel(walls, seed_point[0] - rx1, seed_point[1] - ry1)
    if seed is None:
        return reject("no free pixel near the seed (text sits on the outline / artwork)")

    rh, rw = gray.shape
    flood_mask = np.zeros((rh + 2, rw + 2), np.uint8)
    flood_mask[1:-1, 1:-1] = (walls > 0).astype(np.uint8)
    filled = np.zeros_like(gray)
    cv2.floodFill(filled, flood_mask, seed, 255)
    region = filled == 255

    if region[0, :].any() or region[-1, :].any() or region[:, 0].any() or region[:, -1].any():
        return reject("fill leaked out of the search window (open outline or no outline)")

    contours, _ = cv2.findContours(region.astype(np.uint8) * 255, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return reject("no region")
    largest = max(contours, key=cv2.contourArea)
    x, y, w, h = cv2.boundingRect(largest)
    if w > bw * max_growth or h > bh * max_growth:
        return reject(f"interior {w}x{h}px is more than {max_growth}x the text ({bw}x{bh}px)")
    if w < bw * min_growth or h < bh * min_growth:
        return reject(f"interior {w}x{h}px is smaller than the text ({bw}x{bh}px) - trapped between glyphs")

    hull = np.zeros_like(gray)
    cv2.drawContours(hull, [largest], -1, 255, thickness=-1)       # filled polygon: glyph holes become part of it
    return Interior(
        bbox=(rx1 + x, ry1 + y, rx1 + x + w, ry1 + y + h), hull=hull, origin=(rx1, ry1),
        brightness=float(gray[region].mean()), area=int((hull > 0).sum()),
    )


def detect_bubble_interior(image_bgr, seed_point, ocr_bbox, dark_thresh=180, border_dilate=2, max_growth=2.5):
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
