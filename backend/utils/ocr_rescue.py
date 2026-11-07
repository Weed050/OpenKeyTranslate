# backend/utils/ocr_rescue.py

"""
A second chance for text lines PaddleOCR DETECTED but could not READ (the reading failed the score threshold and the
line was dropped - its bubble then misses that line, or the whole bubble is lost). Two cases seen in the test data:

  1. Letters STACKED one under another ("U / W / A / A / A / !!" in a narrow bubble, Chapter_1/page_009) - lettering,
     not rotated text. PaddleOCR treats every tall, narrow box as a ROTATED line, turns the crop by 90 degrees and reads
     sideways letters ("J3444=" at 0.54). Here the crop is cut into letters along blank rows, the letters are laid out
     side by side on a white strip and that strip is read as one normal line.
  2. A normal line whose detected box came out a pixel or two TOO TIGHT and clipped the letters: "LANDED" on
     Chapter_14/page_017 was read as "LNGEO" (0.48); the same line cropped with a little room around it reads "LANDED"
     at 1.00. Which lines get a tight box changes with the image size (the white margin of ocr_pipeline), so this is
     random - the second reading makes the result independent of it.

A rescued reading is accepted only when it passes the same threshold and plausibility test as any other OCR text
(parse_fn = utils.text_utils.parse_ocr_results), so junk detections stay junk. Boxes overlapping text that was read
fine are skipped, and at most MAX_RESCUES normal lines are re-read per call (time on CPU).

  3. A TILTED line (labels on a map, diagonal captions) that was read only in part: the detector found "THE GREAT" on
     Chapter_1/page_035 (box at -41 degrees) and missed "FOREST" further along the same line. extend_tilted() cuts a
     strip along the line, 3x as long, turns it level and reads it again ("THE GREAT FOREST", 0.99). The new reading is
     kept only when it contains the old text and is longer; pieces of the same line read separately are dropped.

  4. A LEVEL line read UPSIDE DOWN: PaddleOCR's per-line orientation classifier (use_textline_orientation, needed for
     sideways columns) sometimes calls a short level word 180 degrees turned - "I'M" on Chapter_3/page_021 came out as
     "W,1" (I'M upside down), the only flipped box of the page, with the same score either way. unflip_upside_down()
     reads such a box again with the classifier off and keeps that reading unless it is clearly worse.
"""

import re

import cv2
import numpy as np
from rapidfuzz import fuzz

from utils.text_utils import passes_text_threshold

TALL_RATIO = 2.5          # box height / width from which a box counts as a vertical column of letters
MIN_TALL_PX = 36          # ... and at least this tall (3+ letters)
MIN_LETTERS = 3
MIN_LINE_PX = 8           # lower boxes are specks, not text lines
MIN_RESCUE_SCORE = 0.3    # a normal line read worse than this is artwork, not a clipped line
MAX_RESCUES = 12
CROP_BORDER = 12          # white border around a re-read crop (detectors like some air around text)


def _bbox(poly):
    xs = [float(p[0]) for p in poly]
    ys = [float(p[1]) for p in poly]
    return min(xs), min(ys), max(xs), max(ys)


UNFLIP_MARGIN = 0.05      # the upright reading may score this much lower than the flipped one and still win


def _angles(res) -> list:
    try:
        angles = res["textline_orientation_angles"]
    except (KeyError, TypeError, IndexError):
        return []
    return [] if angles is None else list(angles)


def unflip_upside_down(image: np.ndarray, result, items: list[dict], ocr_engine) -> int:
    """
    Case 4 of the module docstring. `items` = parse_fn(result) (the box objects are shared with the raw result, so a
    reading is found by identity). A box counts as flipped when the classifier said 180 degrees (angle 1) and the box is
    level (wider than tall) - a tall box is a sideways column, where the classifier is right to decide. The item's text
    and score are replaced in place; the flipped reading is kept as "text_flipped". Returns how many were fixed.
    """
    by_poly = {id(it["box"]): it for it in items}
    ih, iw = image.shape[:2]
    fixed = 0
    for res in result or []:
        angles = _angles(res)
        if not angles:
            continue
        for text, score, poly, angle in zip(res["rec_texts"], res["rec_scores"], res["dt_polys"], angles):
            if int(angle) != 1:
                continue
            item = by_poly.get(id(poly))
            x1, y1, x2, y2 = _bbox(poly)
            if item is None or x2 - x1 < y2 - y1:
                continue
            crop = image[max(0, int(y1) - 6):min(ih, int(y2) + 6), max(0, int(x1) - 6):min(iw, int(x2) + 6)]
            if crop.size == 0:
                continue
            crop = cv2.copyMakeBorder(crop, 8, 8, 8, 8, cv2.BORDER_REPLICATE)
            try:
                again = ocr_engine.predict(crop, use_textline_orientation=False, use_doc_orientation_classify=False)
            except Exception as e:                  # an older PaddleOCR without the per-call switch: keep the reading
                print(f"[UNFLIP] re-read failed: {type(e).__name__}: {e}")
                return fixed
            readings = sorted(((str(t).strip(), float(s), _bbox(p)) for r in again or []
                               for t, s, p in zip(r["rec_texts"], r["rec_scores"], r["dt_polys"]) if str(t).strip()),
                              key=lambda r: r[2][0])
            if not readings:
                continue
            new_text = " ".join(r[0] for r in readings)
            new_score = min(r[1] for r in readings)
            if new_text != str(text) and new_score >= float(score) - UNFLIP_MARGIN:
                print(f"[UNFLIP] {text!r} ({float(score):.2f}) read upside down -> {new_text!r} ({new_score:.2f})")
                item["text_flipped"], item["text"], item["score"] = str(text), new_text, new_score
                fixed += 1
    return fixed


def _overlap_share(a, b) -> float:
    """Share of box a covered by box b."""
    w = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    h = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    area = max(1.0, (a[2] - a[0]) * (a[3] - a[1]))
    return w * h / area


def rejected_boxes(result, threshold: float, accepted: list[dict]) -> tuple[list, list]:
    """(stacked columns, normal lines) of a raw PaddleOCR result whose reading was REJECTED, as (x1, y1, x2, y2[, score])."""
    taken = [_bbox(it["box"]) for it in accepted]
    stacked, lines = [], []
    for res in result or []:
        for text, score, poly in zip(res["rec_texts"], res["rec_scores"], res["dt_polys"]):
            if passes_text_threshold(text, float(score), threshold):
                continue
            box = _bbox(poly)
            w, h = box[2] - box[0], box[3] - box[1]
            if h < MIN_LINE_PX or any(_overlap_share(box, t) > 0.5 for t in taken):
                continue
            if h >= MIN_TALL_PX and h >= TALL_RATIO * max(w, 1.0):
                stacked.append(box)
            elif float(score) >= MIN_RESCUE_SCORE:
                lines.append((*box, float(score), text))
    lines.sort(key=lambda b: -b[4])
    return stacked, lines[:MAX_RESCUES]


def unstack(crop: np.ndarray) -> np.ndarray | None:
    """
    The letters of a vertical column laid out left to right on a white strip (BGR), or None when the crop does not
    look like stacked letters (fewer than MIN_LETTERS separate rows of ink).
    A short piece right under a letter (the dot of "!" or "?") stays with that letter.
    """
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop
    _, ink = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    if ink.mean() > 127:                         # light letters on a dark background
        ink = 255 - ink
        gray = 255 - gray
    rows = ink.max(axis=1) > 0
    runs, start = [], None
    for i, on in enumerate(np.append(rows, False)):
        if on and start is None:
            start = i
        elif not on and start is not None:
            if i - start >= 2:
                runs.append([start, i])
            start = None
    if len(runs) < MIN_LETTERS:
        return None
    med = float(np.median([e - s for s, e in runs]))
    letters = [runs[0]]
    for s, e in runs[1:]:
        if e - s < 0.45 * med and s - letters[-1][1] <= 0.5 * med:
            letters[-1][1] = e                       # a dot under its letter
        else:
            letters.append([s, e])
    if len(letters) < MIN_LETTERS:
        return None

    pieces = []
    for s, e in letters:
        cols = np.flatnonzero(ink[s:e].max(axis=0) > 0)
        pieces.append(gray[s:e, cols[0]:cols[-1] + 1])
    height = max(p.shape[0] for p in pieces)
    gap = max(2, int(0.25 * med))
    pad = max(6, int(0.5 * height))
    width = sum(p.shape[1] for p in pieces) + gap * (len(pieces) - 1)
    strip = np.full((height + 2 * pad, width + 2 * pad), 255, np.uint8)
    x = pad
    for p in pieces:
        top = pad + (height - p.shape[0]) // 2
        strip[top:top + p.shape[0], x:x + p.shape[1]] = np.minimum(strip[top:top + p.shape[0], x:x + p.shape[1]], p)
        x += p.shape[1] + gap
    return cv2.cvtColor(strip, cv2.COLOR_GRAY2BGR)


def _read(ocr_engine, image, parse_fn, threshold, slice_id):
    """
    Readings of a rescue crop that pass the FULL threshold (no "plausible word" tolerance) and have 2+ letters/digits:
    on 41 test pages the real rescues scored 0.99-1.00, the junk ones ("BT", "mM", "WN", "X") 0.73-0.85.
    The crop is already in the right orientation: the whole-image orientation step must never turn it.
    """
    found = parse_fn(ocr_engine.predict(image, use_doc_orientation_classify=False), slice_id, threshold)
    return [it for it in found if it["score"] >= threshold and sum(c.isalnum() for c in it["text"]) >= 2]


def rescue_rejected(image: np.ndarray, result, ocr_engine, parse_fn, threshold: float, slice_id: int,
                    accepted: list[dict]) -> list[dict]:
    """
    Items {"text", "score", "box", "slice_id", "rescued"} for detected-but-unread lines of `result` (a raw PaddleOCR
    result for `image`), in `image` coordinates. `accepted` = the items already read from that result.
    """
    items = []
    ih, iw = image.shape[:2]
    stacked, lines = rejected_boxes(result, threshold, accepted)
    for x1, y1, x2, y2 in stacked:
        m = 3
        strip = unstack(image[max(0, int(y1) - m):min(ih, int(y2) + m), max(0, int(x1) - m):min(iw, int(x2) + m)])
        if strip is None:
            continue
        found = sorted(_read(ocr_engine, strip, parse_fn, threshold, slice_id), key=lambda it: min(p[0] for p in it["box"]))
        text = "".join(it["text"].replace(" ", "") for it in found)
        if not found or len(text) < MIN_LETTERS:
            continue
        score = sum(it["score"] for it in found) / len(found)
        print(f"[RESCUE] stacked letters read as {text!r} (score {score:.2f}) at {[int(x1), int(y1), int(x2), int(y2)]}")
        items.append({"text": text, "score": score, "slice_id": slice_id, "rescued": "stacked",
                      "box": [[x1, y1], [x2, y1], [x2, y2], [x1, y2]]})

    for x1, y1, x2, y2, old_score, old_text in lines:
        h = y2 - y1
        cx1, cy1 = max(0, int(x1 - max(3, 0.2 * h))), max(0, int(y1 - max(3, 0.3 * h)))
        cx2, cy2 = min(iw, int(x2 + max(3, 0.2 * h)) + 1), min(ih, int(y2 + max(3, 0.3 * h)) + 1)
        b = CROP_BORDER
        crop = cv2.copyMakeBorder(image[cy1:cy2, cx1:cx2], b, b, b, b, cv2.BORDER_CONSTANT, value=[255, 255, 255])
        mx, my = (x1 + x2) / 2 - cx1 + b, (y1 + y2) / 2 - cy1 + b
        # the line itself = the reading over the middle of the crop (not a piece of the line above / below)
        hits = [it for it in _read(ocr_engine, crop, parse_fn, threshold, slice_id)
                if _bbox(it["box"])[0] <= mx <= _bbox(it["box"])[2] and _bbox(it["box"])[1] <= my <= _bbox(it["box"])[3]]
        if not hits:
            continue
        it = max(hits, key=lambda i: i["score"])
        it["box"] = [[float(px) - b + cx1, float(py) - b + cy1] for px, py in it["box"]]
        it["rescued"] = "line"
        print(f"[RESCUE] line re-read as {it['text']!r} (score {it['score']:.2f}; was {old_text!r} {old_score:.2f})")
        items.append(it)
    return items


# ------------------------------------------------------------------ tilted lines

TILT_MIN_DEG = 12          # a line this much off level (and this much off vertical) counts as tilted
TILT_EXTEND = 3.0          # the re-read strip is this many times as long as the detected line ...
TILT_GROW = 1.6            # ... and this many times as tall
MAX_TILTED = 6


def poly_angle(poly) -> float:
    """Angle of a detected text box (top edge, first -> second corner) in degrees, clockwise positive (image y down)."""
    (x0, y0), (x1, y1) = poly[0], poly[1]
    return float(np.degrees(np.arctan2(float(y1) - float(y0), float(x1) - float(x0))))


def is_tilted(angle: float) -> bool:
    a = abs(angle) % 180
    return TILT_MIN_DEG <= a <= 90 - TILT_MIN_DEG or 90 + TILT_MIN_DEG <= a <= 180 - TILT_MIN_DEG


def _norm(text: str) -> str:
    return re.sub(r"[^0-9A-Z]", "", (text or "").upper())


def extend_tilted(image: np.ndarray, items: list[dict], ocr_engine, parse_fn, threshold: float, slice_id: int) -> list[dict]:
    """
    Re-read every tilted item along its own line (see the module docstring, case 3). Changes `items` in place
    (longer text, the box of the whole line, "rescued": "tilted") and returns the list without the pieces the longer
    reading swallowed. Boxes stay four-corner polygons in `image` coordinates.
    """
    ih, iw = image.shape[:2]
    done, swallowed = 0, set()
    for item in items:
        if done >= MAX_TILTED or id(item) in swallowed:
            continue
        poly = np.array(item["box"], np.float32).reshape(-1, 2)
        if len(poly) != 4 or not is_tilted(poly_angle(poly)):
            continue
        tl, tr, br, bl = poly
        length, height = float(np.linalg.norm(tr - tl)), float(np.linalg.norm(bl - tl))
        if length < 10 or height < 6:
            continue
        u, v = (tr - tl) / length, (bl - tl) / height
        c = poly.mean(axis=0)
        hl, hh = length * TILT_EXTEND / 2, height * TILT_GROW / 2
        src = np.array([c - u * hl - v * hh, c + u * hl - v * hh, c + u * hl + v * hh, c - u * hl + v * hh], np.float32)
        w, h = int(2 * hl), int(2 * hh)
        dst = np.array([[0, 0], [w, 0], [w, h], [0, h]], np.float32)
        to_strip = cv2.getPerspectiveTransform(src, dst)
        b = CROP_BORDER
        strip = cv2.copyMakeBorder(cv2.warpPerspective(image, to_strip, (w, h), borderValue=(255, 255, 255)),
                                   b, b, b, b, cv2.BORDER_CONSTANT, value=[255, 255, 255])
        done += 1
        old = _norm(item["text"])
        mid = (w / 2 + b, h / 2 + b)
        best = None
        for it in _read(ocr_engine, strip, parse_fn, threshold, slice_id):
            x1, y1, x2, y2 = _bbox(it["box"])
            new = _norm(it["text"])
            if not (x1 <= mid[0] <= x2 and y1 <= mid[1] <= y2) or len(new) <= len(old):
                continue
            if old not in new and fuzz.partial_ratio(old, new) < 90:
                continue
            if best is None or len(new) > len(_norm(best["text"])):
                best = it
        if best is None:
            continue
        back = np.linalg.inv(to_strip)
        tight = _letters_span(strip, best["box"])
        pts = np.array([[float(px) - b, float(py) - b] for px, py in tight], np.float32).reshape(-1, 1, 2)
        new_poly = cv2.perspectiveTransform(pts, back).reshape(-1, 2)
        new_poly[:, 0] = np.clip(new_poly[:, 0], 0, iw)
        new_poly[:, 1] = np.clip(new_poly[:, 1], 0, ih)
        print(f"[RESCUE] tilted line {item['text']!r} read along its line as {best['text']!r} (score {best['score']:.2f})")
        item.update(text=best["text"], score=best["score"], box=[[float(x), float(y)] for x, y in new_poly], rescued="tilted")
        hull = new_poly.reshape(-1, 1, 2)
        for other in items:                      # separate readings of the same line ("FOREST" found on its own)
            if other is item:
                continue
            ox, oy = np.mean([float(p[0]) for p in other["box"]]), np.mean([float(p[1]) for p in other["box"]])
            if cv2.pointPolygonTest(hull, (float(ox), float(oy)), False) >= 0 and _norm(other["text"]) in _norm(best["text"]):
                swallowed.add(id(other))
    return [it for it in items if id(it) not in swallowed]


def _letters_span(strip: np.ndarray, box) -> list:
    """
    The box of the letters themselves inside a box the recogniser returned on a levelled strip. Its box often runs on
    into the artwork beside a map label (a coastline, the next label), and inpainting erases what the box touches.
    Letters = dark pieces that do not cross the whole box height, about as tall as the others, in one row without a
    wide gap around the middle of the reading. The recogniser's box is kept when no such row is found.
    """
    x1, y1, x2, y2 = (int(round(v)) for v in _bbox(box))
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(strip.shape[1], x2), min(strip.shape[0], y2)
    bh = y2 - y1
    if x2 - x1 < 4 or bh < 4:
        return box
    gray = cv2.cvtColor(strip[y1:y2, x1:x2], cv2.COLOR_BGR2GRAY) if strip.ndim == 3 else strip[y1:y2, x1:x2]
    n, _, st, _ = cv2.connectedComponentsWithStats((gray < 150).astype(np.uint8), connectivity=8)
    parts = [st[i] for i in range(1, n)
             if st[i][4] >= max(4, 0.01 * bh * bh) and st[i][3] <= 0.95 * bh and st[i][2] <= 4 * bh
             and not (st[i][1] <= 0 and st[i][1] + st[i][3] >= bh)]
    if len(parts) < 2:
        return box
    hm = float(np.median([p[3] for p in parts]))
    cy = float(np.median([p[1] + p[3] / 2 for p in parts]))
    row = sorted((p for p in parts if abs(p[1] + p[3] / 2 - cy) <= 0.4 * hm and p[3] >= 0.4 * hm), key=lambda p: p[0])
    if not row:
        return box
    mid = (x2 - x1) / 2
    start = min(range(len(row)), key=lambda i: abs(row[i][0] + row[i][2] / 2 - mid))
    lo = hi = start
    while lo > 0 and row[lo][0] - (row[lo - 1][0] + row[lo - 1][2]) <= 1.2 * hm:
        lo -= 1
    while hi < len(row) - 1 and row[hi + 1][0] - max(r[0] + r[2] for r in row[lo:hi + 1]) <= 1.2 * hm:
        hi += 1
    keep = row[lo:hi + 1]
    pad = max(2.0, 0.2 * hm)
    lx1 = x1 + min(p[0] for p in keep) - pad
    lx2 = x1 + max(p[0] + p[2] for p in keep) + pad
    ly1 = y1 + min(p[1] for p in keep) - pad
    ly2 = y1 + max(p[1] + p[3] for p in keep) + pad
    return [[lx1, ly1], [lx2, ly1], [lx2, ly2], [lx1, ly2]]


def tilted_text_rect(points_by_item: list) -> list | None:
    """
    [cx, cy, w, h, angle] of tilted text (w along the text, angle clockwise in degrees) from the four-corner boxes of
    its words, or None when the text is level / the words disagree. Used by typesetting to letter a map label along
    its original slant.
    """
    polys = [np.array(p, np.float32).reshape(-1, 2) for p in points_by_item if len(p) == 4]
    if not polys:
        return None
    angles = [poly_angle(p) for p in polys]
    med = float(np.median(angles))
    if not is_tilted(med) or max(abs(a - med) for a in angles) > 12:
        return None
    (cx, cy), (rw, rh), _ = cv2.minAreaRect(np.concatenate(polys))
    long_side, short_side = max(rw, rh), min(rw, rh)
    return [round(float(cx), 1), round(float(cy), 1), round(float(long_side), 1), round(float(short_side), 1), round(med, 1)]
