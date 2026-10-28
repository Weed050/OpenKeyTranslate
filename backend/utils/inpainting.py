
#  backend/utils/inpainting.py

"""
Image processing utilities for erasing original text from manga/webtoon panels.

Two sources of "what to erase":
  1. the OCR text-line boxes (dilated a little) - as before;
  2. NEW: inside every bubble whose white interior could be verified (utils/bubble_interior.py), any remaining dark
     pixels that no OCR box covered. This is the usual symptom of a bubble OCR only half-read (lines 1, 2, 4, 6
     detected, 3 and 5 not): the missed lines used to stay on the page in English. Only ADDS to the mask, only inside a
     bright (white) interior, never on the outline - so artwork outside bubbles is untouched. (It also answers the old
     TODO about confining the cleaning to the bubble.)

The same pass reports, per bubble, the interior box and whether text pixels were left over (`missed_text`) - the editor
shows "check text" and the pipeline re-OCRs such bubbles (services/bubble_refine.py).

Still OpenCV TELEA for the fill itself.
TODO: evaluate a generative inpainting model (e.g. LaMa) - TELEA smudges edges over artwork.
"""

import numpy as np
import cv2

from utils.bubble_interior import find_interior

MIN_WHITE_BRIGHTNESS = 200      # only "white" bubbles get the extra erase; toned / dark boxes are left to the OCR boxes
LEFTOVER_MIN_PIXELS = 40        # dark pixels not covered by any OCR box ...
LEFTOVER_MIN_RATIO = 0.004      # ... and at least this share of the interior => "missed text"


def _poly(box) -> np.ndarray:
    return np.array(box, np.int32).reshape((-1, 1, 2))


def analyze_bubble(image: np.ndarray, b: dict):
    """
    Interior check of ONE bubble: {"bubble_id", "bbox", "boxes"} (original coords).
    :return: (Interior | None, info, leftover) ; leftover = ROI-sized bool mask of dark pixels inside the interior that
             no OCR box covers (None when there is no interior).
    """
    it = find_interior(image, _center(b["bbox"]), b["bbox"])
    if it is None:
        return None, {"interior_box": None, "missed_text": False, "extra_pixels": 0}, None

    rx, ry = it.origin
    rh, rw = it.hull.shape
    roi = image[ry:ry + rh, rx:rx + rw]
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY) if roi.ndim == 3 else roi

    covered = np.zeros((rh, rw), np.uint8)
    for box in b.get("boxes", []):
        shifted = np.array(box, np.float32) - np.array([rx, ry], np.float32)
        cv2.fillPoly(covered, [_poly(shifted)], 255)
    covered = cv2.dilate(covered, np.ones((5, 5), np.uint8), iterations=1)

    leftover = (gray < 180) & (it.hull > 0) & (covered == 0)
    n_left = int(leftover.sum())
    missed = n_left >= max(LEFTOVER_MIN_PIXELS, LEFTOVER_MIN_RATIO * it.area)
    return it, {"interior_box": list(it.bbox), "missed_text": bool(missed), "extra_pixels": n_left}, leftover


def build_erase_mask(image: np.ndarray, boxes: list, bubbles: list | None = None, use_interior: bool = True):
    """
    :param image:   original BGR image.
    :param boxes:   polygons (original coordinates) of every text line to erase.
    :param bubbles: [{"bubble_id", "bbox": (x1,y1,x2,y2), "boxes": [polygons of THIS bubble's lines]}] (original coords)
                    - the bubbles whose interior should be analysed. None / [] = boxes only.
    :return: (mask uint8, info) ; info = {bubble_id: {"interior_box": [x1,y1,x2,y2] | None, "missed_text": bool,
             "extra_pixels": int}}
    """
    mask = np.zeros(image.shape[:2], dtype=np.uint8)
    for box in boxes:
        cv2.fillPoly(mask, [_poly(box)], 255)
    # dilate to cover anti-aliasing and JPG artifacts around the letters
    mask = cv2.dilate(mask, np.ones((5, 5), np.uint8), iterations=1)

    info: dict = {}
    if not (use_interior and bubbles):
        return mask, info

    for b in bubbles:
        it, bubble_info, leftover = analyze_bubble(image, b)
        info[b["bubble_id"]] = bubble_info
        if it is not None and bubble_info["missed_text"] and it.brightness >= MIN_WHITE_BRIGHTNESS:
            rx, ry = it.origin
            rh, rw = it.hull.shape
            # grow the leftover a bit (anti-aliasing) but never past the outline: stay inside the hull + 2 px of walls
            grown = cv2.dilate(leftover.astype(np.uint8) * 255, np.ones((5, 5), np.uint8), iterations=1)
            inside = cv2.dilate(it.hull, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)), iterations=1)
            extra = cv2.bitwise_and(grown, inside)
            mask[ry:ry + rh, rx:rx + rw] = cv2.bitwise_or(mask[ry:ry + rh, rx:rx + rw], extra)
    return mask, info


def _center(bbox):
    return (bbox[0] + bbox[2]) / 2.0, (bbox[1] + bbox[3]) / 2.0


def inpaint(image: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """Fill the masked pixels from their surroundings (OpenCV TELEA)."""
    return cv2.inpaint(image, mask, 5, cv2.INPAINT_TELEA)


def erase_text_from_image(image_original, grouped_lines, to_original_coords, bubbles=None, use_interior=False):
    """
    Remove detected text from an image. Kept signature-compatible (test_main.py): with no `bubbles` it behaves as before -
    OCR line boxes only. The API pipeline passes `bubbles` to enable the bubble-interior pass.
    """
    boxes = [to_original_coords(line["box"]) for line in grouped_lines]
    mask, _ = build_erase_mask(image_original, boxes, bubbles, use_interior)
    return inpaint(image_original, mask)
