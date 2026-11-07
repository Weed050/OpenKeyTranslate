
# backend/services/ocr_pipeline.py

"""
OCR Pipeline Orchestration Module
_________________________________

This module serves as the core orchestration engine of ocr functions (from /utils/) which
extracts, corrects, and structurizes text from images.
It is specifically designed and optimized for extreme vertical layouts
(e.g., webtoons, continuous manga strips) that typically overwhelm and break
standard OCR engines.

To bypass the inherent downscaling constraints of standard models (like PaddleOCR),
this pipeline implements a robust Overlapping Sliding-Window (slicing) architecture.

Pipeline Stages:
    1. Pre-processing: Upscales the source canvas to maximize fine text legibility (if SCALE != 1.0).
    2. Slicing: Divides the giant image into overlapping horizontal segments.
    3. Marker logic: Injects tracking markers into an artificial "safe zone"
       (canvas padding) to monitor for and correct OCR-induced layout rotations.
    4. OCR Extraction: Executes GPU-accelerated recognition on each full-res slice.
    5. Consolidation: Maps local bounding box coordinates back to the global page space.
    6. Deduplication: Intelligently resolves text overlap at slice boundaries.
    7. Spatial Layout Analysis: Merges individual words into cohesive text lines,
       and lines into distinct speech bubbles, while filtering out visual noise.
"""

# Laptop environmental overrides (Preserved for alternate CPU execution)
# import os
# os.environ['OMP_NUM_THREADS'] = '3'
# os.environ['FLAGS_allocator_strategy'] = 'naive_best_fit'
# os.environ['FLAGS_fraction_of_gpu_memory_to_use'] = '0'
# os.environ['KMP_DUPLICATE_LIB_OK'] = 'TRUE'

import cv2
import numpy as np
import logging
from contextlib import contextmanager

from core.ocr_runtime import apply_env, paddle_kwargs
apply_env()   # environment flags / cache folders MUST be set before paddle is imported
from paddleocr import PaddleOCR  # noqa: E402

from core.config import (

    SCALE,

    # SCALE - Image upscale factor used to improve OCR accuracy for small text/fonts.
    # A scale of 1.5 enlarges the image by 150%, which moves fine text into the
    # optimal recognition range of the OCR model.
    #
    # Note: Increasing this factor expands the total pixel area quadratically (e.g., 1.5x scale
    # results in 2.25x more pixels), which significantly increases VRAM usage and processing time.

    WHITE_SPACE, WHITE_SPACE_PAD_WIDTH,
    # WHITE_SPACE - Boolean flag enabling artificial canvas padding.
    # When True, dynamically appends an empty white margin to the right side of the image.
    # This creates a "safe zone" for injecting the tracking marker, ensuring it never
    # covers, overlaps, or interferes with the actual manga/comic text.
    #
    # WHITE_SPACE_PAD_WIDTH - The width (in pixels) of the artificial margin.

    TEXT_THRESHOLD,
    DEDUP_WORD_THRESH, DEDUP_GEO_THRESH, DEDUP_MIN_OVERLAP_PX
)
from utils.draw_debug import make_marker_record
from utils.handle_duplicates import smart_deduplicate_by_lines, remove_slice_boundary_duplicates
from utils.merge_boxes import build_text_lines, group_lines_into_bubbles, \
    filter_noise_lines, filter_noise_bubbles
from utils.ignore_filter import filter_ignored_bubbles
from utils.bubble_post import apply_bubble_postprocess
from services.bubble_refine import refine_bubbles
from core.config import get_setting
from utils.marker import (
    inject_marker, resolve_marker_state, detect_rotation,
    correct_boxes_by_angle, remove_marker, pad_crop_right
)
from utils.text_utils import parse_ocr_results, passes_text_threshold, is_any_marker
from utils.ocr_rescue import rescue_rejected, extend_tilted, unflip_upside_down

# Suppress PaddleOCR info and warnings; log errors only
logging.getLogger("ppocr").setLevel(logging.ERROR)


# ---------------- OCR INITIALIZATION ----------------
class PageOCR:
    """
    The PaddleOCR engine with its whole-image orientation step decided PER CALL instead of once at start-up.

    PaddleOCR 3 runs a document-orientation classifier (0/90/180/270) on every image it gets - here every slice of a
    page and every bubble crop. Manga pages are upright, yet on 26 test pages it turned 12 of 67 slices, and recognition
    then ran on rotated text ("SIHI" for "THIS", lost lines; the marker logic only repairs the box coordinates).
    So it is OFF by default (setting ocr_detect_page_rotation). A page whose whole scan IS sideways is re-OCR'd with it
    on (editor Re-OCR -> "the whole page is rotated", kept as detect_rotation in the page JSON). Sideways text INSIDE an
    upright page does not need it: tall text lines are turned and 0/180-checked line by line (use_textline_orientation).
    """

    def __init__(self, engine):
        self.engine = engine
        self.override = None          # the page's own choice while it is processed (page_rotation()); None = setting

    def detect_rotation(self) -> bool:
        return self.override if self.override is not None else bool(get_setting("ocr_detect_page_rotation", False))

    def predict(self, image, **kwargs):
        kwargs.setdefault("use_doc_orientation_classify", self.detect_rotation())
        return self.engine.predict(image, **kwargs)

    def __getattr__(self, name):
        return getattr(self.engine, name)


# Device (CPU / GPU), oneDNN, threads and extra Paddle flags come from settings.json - see core/ocr_runtime.py.
# (Before: edit device='gpu' here and comment/uncomment four os.environ lines to switch builds.)
ocr = PageOCR(PaddleOCR(**paddle_kwargs()))


@contextmanager
def page_rotation(detect: bool | None):
    """Every OCR call inside the block uses the page's own choice (True / False); None = the setting. Use under OCR_LOCK."""
    previous, ocr.override = ocr.override, detect
    try:
        yield
    finally:
        ocr.override = previous



# ---------------- CORE FUNCTIONS ----------------

# ---------------- SCALE BACK BOXES ----------------
def to_original_coords(box):
    """Scale box coordinates back to original image size."""
    return [[x / SCALE, y / SCALE] for x, y in box]


# ---------------- SLICE HEIGHT, CUT PLACES ----------------
# PaddleOCR 3 (PP-OCRv5) does NOT shrink an image for detection until its longer side passes 4000 px (limit_type "min",
# max_side_limit 4000) - the 960 px limit this slicing was written against is gone. Measured on 26 pages of 1200-1842 px
# (chapters 2-14 of the test data): one pass per page read at least as well as 2-3 slices (fewer junk pieces of cut lines,
# e.g. "EL" as its own bubble, "ISS" for "IS"), at the same speed. So pages up to ocr_slice_max_h are read in ONE pass;
# only taller images (webtoon strips) are sliced, and they are cut where no text can be cut:
#   1. in a BLANK BAND (gutter between panels: rows with almost no ink) in the lower half of the planned slice;
#   2. without such a band, the next slice starts above every line the previous slice saw crossing the cut, so each line
#      is read whole at least once; pieces of lines cut at the bottom edge are dropped when the next slice has them whole.
SLICE_TARGET_H = 1200         # slice height for images taller than ocr_slice_max_h. 6 webtoon-like strips (4 pages stacked):
                              # 1000 px slices cut in blank bands -> 10 word errors, 2000 px -> 20, the old fixed slicing -> 16
SLICE_BAND_MIN_ROWS = 6       # a blank band must be at least this tall (gaps between lines in a bubble are ~3-6 px)
SLICE_BAND_MAX_EDGES = 6      # ... with at most this many strong left-right changes per row (a bubble outline crossing = 4)
SLICE_EDGE_PX = 3             # a text box this close to an inner slice edge was cut by that edge


def get_overlap_size(image_height):
    """
    Slice height and the FALLBACK overlap (used only where no blank band is found) for an image this tall.
    Images up to the setting ocr_slice_max_h (default 2000 px of the scaled image; 0 = never slice) are read in one pass:
    (image_height, 0). Taller ones get slices of SLICE_TARGET_H (or ocr_slice_max_h when that is smaller).

    :return: slice_h - slice height, overlap - overlap height (how much 2 slices need to overlap)
    """
    max_h = int(get_setting("ocr_slice_max_h", 2000) or 0)
    if max_h <= 0 or image_height <= max_h:
        return image_height, 0
    slice_h = max(600, min(max_h, SLICE_TARGET_H))
    overlap = min(max(int(slice_h * 0.08), 60), 240)
    return slice_h, overlap


def row_edges(image: np.ndarray) -> np.ndarray:
    """Per image row: how many strong left-right brightness changes it has. ~0 = blank row, dozens = text / screentone."""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    return (np.abs(np.diff(gray.astype(np.int16), axis=1)) > 40).sum(axis=1)


def find_blank_band(edges: np.ndarray, lo: int, hi: int, min_rows: int = SLICE_BAND_MIN_ROWS,
                    max_edges: int = SLICE_BAND_MAX_EDGES) -> tuple[int, int] | None:
    """The lowest run of at least min_rows quiet rows inside [lo, hi): (start, end) in image rows, or None."""
    quiet = np.concatenate(([False], edges[lo:hi] <= max_edges, [False]))
    changes = np.flatnonzero(np.diff(quiet.astype(np.int8)))
    runs = [(lo + s, lo + e) for s, e in zip(changes[::2], changes[1::2]) if e - s >= min_rows]
    return runs[-1] if runs else None


def _y_range(item) -> tuple[float, float]:
    ys = [p[1] for p in item["box"]]
    return min(ys), max(ys)


def next_slice_start(items: list[dict], y: int, y_end: int, planned: int, slice_h: int) -> int:
    """
    Where the next slice starts: `planned`, moved up above every text box of this slice that the line `planned` would
    cut, so the next slice reads that line whole too. Never above y + slice_h // 2 (the slicing must move on).
    `items` are in page coordinates.
    """
    floor = y + slice_h // 2
    start = planned
    for _ in range(8):                                   # moving up can hit the line above: repeat until stable
        crossing = [top for top, bottom in map(_y_range, items) if top < start < bottom]
        if not crossing:
            break
        start = max(floor, int(min(crossing)) - SLICE_EDGE_PX * 2)
        if start == floor:
            break
    return max(floor, min(start, y_end - 1))


# ---------------- ONE SLICE ----------------
def orientation_of(result) -> tuple[int, np.ndarray] | None:
    """
    (angle, turned image) of PaddleOCR's whole-image orientation step for a raw predict() result, or None when this
    PaddleOCR version does not report it. Boxes of that result are in the TURNED image.
    """
    try:
        pre = result[0]["doc_preprocessor_res"]
        angle, turned = int(pre["angle"]), pre["output_img"]
    except (KeyError, IndexError, TypeError, ValueError):
        return None
    return (angle, turned) if angle in (0, 90, 180, 270) and turned is not None else None


def turn_boxes_back(items: list[dict], angle: int, turned_shape) -> list[dict]:
    """
    Map boxes from the image the orientation step turned by `angle` back to the input image. Checked on 9 turned pages
    (90 / 180 / 270, three chapters): 1-3 px off. (utils/marker.correct_boxes_by_angle uses the marker's own angle
    convention and does NOT fit PaddleOCR's reported 90 / 270.)
    """
    oh, ow = turned_shape[:2]
    back = {90: lambda x, y: (oh - y, x), 180: lambda x, y: (ow - x, oh - y), 270: lambda x, y: (y, ow - x)}.get(angle)
    if back:
        for it in items:
            it["box"] = [list(back(float(x), float(y))) for x, y in it["box"]]
    return items


def rejected_readings(result, slice_id: int) -> list[dict]:
    """
    Readings of a raw PaddleOCR result that FAILED the text threshold (parse_ocr_results dropped them), as items
    {"text", "score", "box", "slice_id"} - for the editor's OCR debug view only (Settings > Editor), never used further.
    """
    out = []
    for res in result or []:
        for text, score, poly in zip(res["rec_texts"], res["rec_scores"], res["dt_polys"]):
            if is_any_marker(text, slice_id) or passes_text_threshold(text, score, TEXT_THRESHOLD):
                continue
            out.append({"text": str(text), "score": float(score), "slice_id": slice_id,
                        "box": [[float(px), float(py)] for px, py in poly]})
    return out


def _ocr_slice(crop_original: np.ndarray, slice_id: int, y: int, marker_records: list,
               rejected: list | None = None) -> list[dict]:
    """
    OCR one slice; items in slice coordinates (boxes may reach into the white margin - the caller clips them).
    White margin on the right (settings ocr_white_space / ocr_white_space_pad_width, on by default): kept as it was.
    On 41 test pages it was neither better nor worse - small text is found or lost depending on the image size either
    way (with it "WHAT ?!" on page_014 was found, without it "RE-TREAT !!" on page_009).
    Orientation step on: PaddleOCR reports the angle it turned the slice by, the boxes are turned back. Only when it
    does not report it, the old tracking marker is used (_ocr_slice_with_marker).
    rejected: when a list is given, the readings that failed the threshold are appended (slice coordinates, OCR debug).
    """
    pad = WHITE_SPACE_PAD_WIDTH if WHITE_SPACE else 0
    canvas = pad_crop_right(crop_original, pad) if pad else crop_original
    result = ocr.predict(canvas)
    angle, turned = 0, canvas
    if ocr.detect_rotation():
        info = orientation_of(result)
        if info is None:
            return _ocr_slice_with_marker(crop_original, slice_id, y, marker_records)
        angle, turned = info
    items = parse_ocr_results(result, slice_id, TEXT_THRESHOLD)
    unflip_upside_down(turned, result, items, ocr)          # a level word the line classifier turned 180 deg ("W,1" = I'M)
    items += rescue_rejected(turned, result, ocr, parse_ocr_results, TEXT_THRESHOLD, slice_id, items)   # boxes in `turned`
    items = extend_tilted(turned, items, ocr, parse_ocr_results, TEXT_THRESHOLD, slice_id)               # diagonal lines read whole
    lost = rejected_readings(result, slice_id) if rejected is not None else []
    if angle:
        print(f"[SLICE {slice_id}] orientation step turned the slice by {angle} deg - boxes turned back")
        turn_boxes_back(items, angle, turned.shape)
        turn_boxes_back(lost, angle, turned.shape)
    if rejected is not None:
        rejected.extend(lost)
    return items


def _ocr_slice_with_marker(crop_original: np.ndarray, slice_id: int, y: int, marker_records: list) -> list[dict]:
    """
    FALLBACK for a PaddleOCR version that does not report the orientation angle: a marker in a white margin tracks
    whether PaddleOCR turned the slice, and the boxes are turned back (see "How marker-based layout correction works"
    in run_ocr_sliced). Returns the slice items in slice coordinates, marker removed; appends a debug record.
    """
    content_w = crop_original.shape[1]
    pad_width = WHITE_SPACE_PAD_WIDTH if WHITE_SPACE else 0

    # 1. Prepare slice canvas and inject tracking marker
    if WHITE_SPACE:
        ocr_crop = pad_crop_right(crop_original, pad_width)
        ocr_crop, _, marker_pos = inject_marker(
            ocr_crop, slice_id, content_width=content_w
        )
    else:
        ocr_crop, _, marker_pos = inject_marker(crop_original.copy(), slice_id)

    # 2. Execute OCR engine (performed on ocr_crop sliced image)
    result = ocr.predict(ocr_crop)
    slice_items = parse_ocr_results(result, slice_id, TEXT_THRESHOLD)

    # 3. Initial filter to extract text items without the marker
    text_items_temp = remove_marker(slice_items, slice_id)

    # 4.1 Resolve marker states (handles logic adjustments and re-shots internally)
    marker_item, found, marker_pos, ocr_crop, slice_items = resolve_marker_state(
        ocr,
        crop_original,
        ocr_crop,
        slice_items,
        slice_id,
        marker_pos,
        text_items_temp,
        TEXT_THRESHOLD,
        pad_width=pad_width,
    )

    # 4.2 Refresh localized states following resolver modifications
    ocr_shape = ocr_crop.shape[:2]
    text_items_temp = remove_marker(slice_items, slice_id)

    # 5. Handle rotation correction using the tracking marker position (padded with WHITE_SPACE)
    angle = 0
    if found and len(text_items_temp) > 0:
        angle = detect_rotation(
            marker_item, marker_pos, ocr_shape, content_width=content_w if pad_width else None
        )
        if angle != 0:
            print(f"[SLICE {slice_id}] {angle} DEG ROTATION DETECTED")
            slice_items = correct_boxes_by_angle(slice_items, angle, ocr_shape)
    else:
        print(f"[SLICE {slice_id}] ROTATION correction skipped: no meaningfull text found in slice")

    # 6. Logging, data cleanup
    marker_records.append(make_marker_record(slice_id, y, marker_pos, marker_item, angle))
    return remove_marker(slice_items, slice_id)


# ---------------- SLICING, OCR ----------------
def run_ocr_sliced(image: np.ndarray, debug: dict | None = None) -> tuple[list[dict], list[tuple[int, int]], list[dict]]:
    """
    Run OCR on an image using an overlapping horizontal slicing pipeline.

    Design reason:
        Standard PaddleOCR automatically downscales ultra-tall or high-resolution images
        (often constraining the max side to 960px or 1500px depending on backend configurations).
        For extreme vertical layouts like webtoons or compiled manga strips (e.g., 15,000px high),
        this native downscaling completely obliterates text legibility, rendering OCR useless.

        To bypass this limitation, this custom slicing method breaks giant images into
        manageable, overlapping horizontal segments. This ensures PaddleOCR processes each
        slice at native, uncompromised resolution (multiplied by SCALE).

    Mechanics:
        1. Image Slicing: only images taller than ocr_slice_max_h are sliced (see get_overlap_size); cuts go into blank
           bands, otherwise the next slice starts above every line the cut would clip (next_slice_start).
        2.-3. Orientation (PageOCR; off by default): PaddleOCR reports the angle it turned a slice by and the boxes
           are turned back (_ocr_slice). The marker below is only the FALLBACK when no angle is reported:
           Canvas Padding: If WHITE_SPACE is enabled, appends a temporary blank margin to
           the right side of the slice. This provides a safe zone for marker injection.
        3. Marker Injection: Injects alignment tracking markers into the safe zone to detect
           and correct unexpected local rotations caused by the OCR engine.
        4. Processing: Executes the OCR engine on individual slices at full detail.
        5. Consolidation: Strips the temporary padding and maps the localized text bounding
           boxes back to global, page-level coordinates.

    How marker-based layout correction works:
        1. Inject a marker and record its initial image coordinates.
        2. Execute the process.
        3. Locate the marker searching through OCR output.
        4. Calculate the coordinate difference (offset).
        5. Correct the elements based on the calculated variance.

    Args:
        image (np.ndarray): The pre-scaled (multiplied by SCALE) source image canvas.
        debug (dict | None): when given, debug["rejected"] gets the readings that failed the threshold (page
            coordinates) and debug["cut"] the pieces of lines dropped at a slice's bottom edge - the OCR debug view.

    :return:
    tuple: A structural tuple containing:
    - list[dict]: Adjusted OCR text items (text, confidence score, global box, slice_id).
    - list[tuple[int, int]]: Slice boundaries mapped as (y_start, y_end) coordinates.
    - list[dict]: Marker state debug records used for rotation and alignment validation.
    """

    h, w = image.shape[:2]

    slice_h, overlap = get_overlap_size(h)
    edges = row_edges(image) if slice_h < h else None

    all_items = []
    y = 0
    slice_id = 0
    slices = []
    marker_records = []

    while y < h:
        y_end = min(y + slice_h, h)
        band = None
        if y_end < h:
            band = find_blank_band(edges, y + slice_h // 2, y_end)
            if band:                                     # this slice ends at the bottom of the gutter, the next starts
                y_end = band[1]                          # at its top: they overlap only in blank rows
        slices.append((y, y_end))

        # Crop current slice from the image
        crop_original = image[y:y_end, :].copy()
        content_w = crop_original.shape[1]
        print(f"\n[SLICE {slice_id}] {y}:{y_end}")

        lost = [] if debug is not None else None
        slice_items = _ocr_slice(crop_original, slice_id, y, marker_records, lost)

        # Clip X coordinates to content width to discard tracking padding area,
        # 7. then map coordinates from localized slice space to global page coordinates
        for item in slice_items + (lost or []):
            item["box"] = [[min(max(0.0, float(px)), float(content_w)), float(py) + y] for px, py in item["box"]]
        if debug is not None:
            debug.setdefault("rejected", []).extend(lost)

        if y_end >= h:
            all_items.extend(slice_items)
            break

        # 8. Next slice: from the band's top (nothing to cut there) or `overlap` above the end, moved up above any line
        #    this slice saw crossing that place. Pieces of lines cut by this slice's bottom edge are dropped when the next
        #    slice reads them whole (it starts above them); boundary dedup handles whole lines read twice.
        planned = band[0] if band else y_end - overlap
        next_y = next_slice_start(slice_items, y, y_end, planned, slice_h)
        cut = [it for it in slice_items if _y_range(it)[1] >= y_end - SLICE_EDGE_PX and _y_range(it)[0] >= next_y]
        if cut:
            print(f"[SLICE {slice_id}] {len(cut)} line(s) cut by the bottom edge - read whole by the next slice: "
                  + ", ".join(repr(it["text"]) for it in cut))
        all_items.extend(it for it in slice_items if not any(it is c for c in cut))
        if debug is not None:
            debug.setdefault("cut", []).extend(cut)
        y = next_y
        slice_id += 1

    return all_items, slices, marker_records



# ---------------- OCR OF ONE REGION (a single bubble) ----------------
def ocr_region(image: np.ndarray, region) -> list[dict]:
    """
    OCR a rectangle (x1, y1, x2, y2) of `image` and return the items in IMAGE coordinates.
    The caller must hold OCR_LOCK when not called from inside process_image (which is already under it).

    Used to re-read a bubble as one crop (services/bubble_refine.py, the editor's "Re-OCR bubble" / "Add bubble"):
    bigger text relative to the crop and a clean background find lines the full-page pass missed.
    """
    h, w = image.shape[:2]
    x1, y1, x2, y2 = max(0, int(region[0])), max(0, int(region[1])), min(w, int(region[2])), min(h, int(region[3]))
    if x2 - x1 < 8 or y2 - y1 < 8:
        return []
    pad = 12                                                     # white margin: detectors like some air around text
    crop = cv2.copyMakeBorder(image[y1:y2, x1:x2], pad, pad, pad, pad, cv2.BORDER_CONSTANT, value=[255, 255, 255])
    result = ocr.predict(crop)
    angle, turned = (orientation_of(result) or (0, crop)) if ocr.detect_rotation() else (0, crop)
    items = parse_ocr_results(result, -1, TEXT_THRESHOLD)
    unflip_upside_down(turned, result, items, ocr)
    items += rescue_rejected(turned, result, ocr, parse_ocr_results, TEXT_THRESHOLD, -1, items)
    items = extend_tilted(turned, items, ocr, parse_ocr_results, TEXT_THRESHOLD, -1)
    turn_boxes_back(items, angle, turned.shape)          # orientation step on: boxes back into the crop
    ch, cw = crop.shape[:2]
    shifted = []
    for it in items:
        cx = sum(p[0] for p in it["box"]) / len(it["box"])
        cy = sum(p[1] for p in it["box"]) / len(it["box"])
        if not (-0.2 * cw <= cx <= 1.2 * cw and -0.2 * ch <= cy <= 1.2 * ch):
            print("[OCR REGION] box outside the crop (the orientation step rotated the coordinates?) - result discarded.")
            return []
        it["box"] = [[float(px) - pad + x1, float(py) - pad + y1] for px, py in it["box"]]
        shifted.append(it)
    return shifted


# =====================================================================
# THE BLACK BOX
# =====================================================================

def process_image(image_bgr: np.ndarray, protected_terms=(), detect_rotation: bool | None = None) -> dict:
    """
    Main entry point for the OCR module.
    Accepts an image, processes it entirely, and returns structured data.

    protected_terms: names (glossary source terms) the post-OCR text cleanup must never alter.
    detect_rotation: the page's own choice for the whole-image orientation step (see PageOCR); None = the setting.
    """
    with page_rotation(detect_rotation):
        return _process_image(image_bgr, protected_terms)


def _bbox(poly) -> tuple[float, float, float, float]:
    xs = [float(p[0]) for p in poly]
    ys = [float(p[1]) for p in poly]
    return min(xs), min(ys), max(xs), max(ys)


def _covered(a, b) -> float:
    """Share of box a covered by box b."""
    w = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    h = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    return w * h / max(1.0, (a[2] - a[0]) * (a[3] - a[1]))


def _removed(before: list[dict], after: list[dict], why: str, box_key: str = "box") -> list[dict]:
    """
    OCR debug: entries of `before` a filter took out, {"text", "score", "box", "why"}. An entry replaced by a trimmed /
    re-read copy (dedup returns new dicts for those) is not "removed" - something in `after` still covers its place.
    """
    kept = {id(x) for x in after}
    boxes = [_bbox(x[box_key]) for x in after]
    out = []
    for x in before:
        if id(x) in kept:
            continue
        own = _bbox(x[box_key])
        if any(_covered(own, b) > 0.6 for b in boxes):
            continue
        out.append({"text": x.get("text", ""), "score": float(x.get("score", x.get("avg_score", 0)) or 0),
                    "box": [list(map(float, p)) for p in x[box_key]], "why": why})
    return out


def _process_image(image_bgr: np.ndarray, protected_terms=()) -> dict:
    image_scaled = cv2.resize(image_bgr, None, fx=SCALE, fy=SCALE)
    image_width = image_scaled.shape[1]
    _, overlap = get_overlap_size(image_scaled.shape[0])

    print("Running OCR Pipeline...\n")
    # what the OCR saw but did not use, for the editor's OCR debug view (cheap: the raw results are already there)
    debug = {"rejected": [], "cut": []}
    items, slices, marker_records = run_ocr_sliced(image_scaled, debug)
    dropped = [{"text": it["text"], "score": float(it["score"]), "box": it["box"],
                "why": "cut by a slice edge (read whole in the next slice)"} for it in debug["cut"]]

    before = items
    items = remove_slice_boundary_duplicates(items, max_y_jitter=int(overlap * 1.5))
    dropped += _removed(before, items, "read twice where two slices overlap")
    grouped_lines = build_text_lines(items, image_width=image_width)

    print("\n=== POST-PROCESSING: PAGE-LEVEL DEDUPLICATION ===")
    before = items
    items = smart_deduplicate_by_lines(
        grouped_lines, items, image_scaled, ocr,
        word_thresh=DEDUP_WORD_THRESH,
        geo_thresh=DEDUP_GEO_THRESH,
        min_overlap_area=DEDUP_MIN_OVERLAP_PX
    )
    dropped += _removed(before, items, "duplicate of an overlapping reading")

    grouped_lines = build_text_lines(items, image_width=image_width)
    before = grouped_lines
    grouped_lines = filter_noise_lines(grouped_lines)
    dropped += _removed(before, grouped_lines, "noise line (single letter / junk)")
    bubbles = group_lines_into_bubbles(grouped_lines)
    before = bubbles
    bubbles = filter_noise_bubbles(bubbles)
    dropped += _removed(before, bubbles, "noise bubble (single letter / junk)", "box_coords")
    before = bubbles
    bubbles = filter_ignored_bubbles(bubbles)  # watermarks / URLs from settings.json -> ocr_ignore_patterns
    dropped += _removed(before, bubbles, "matches an ignore pattern (Settings > OCR: ignored text)", "box_coords")

    # Bubble geometry fixes (merge bubbles OCR cut into pieces, re-read half-detected ones). Optional and fail-safe:
    # whatever goes wrong here must not cost the user the OCR result.
    if get_setting("ocr_refine", True):
        partial = {"items": items, "lines": grouped_lines, "bubbles": bubbles}
        try:
            stats = refine_bubbles(image_scaled, partial, ocr_region, scan=get_setting("ocr_scan_missed_bubbles", False))
            items, grouped_lines, bubbles = partial["items"], partial["lines"], partial["bubbles"]
            if any(stats.values()):
                print(f"[REFINE] numbers split {stats['numbers']}, split {stats['split']}, merged {stats['merged']}, found {stats['found']} missed bubble(s), "
                      f"re-read {stats['refined']} half-detected bubble(s).")
        except Exception as e:
            print(f"[REFINE] skipped after an error: {type(e).__name__}: {e}")

    # Order of the bubbles for the translator / the LLM (setting bubble_order; ids are NOT renamed). After refine on
    # purpose: split / merge renumber bubbles by vertical position and would undo a right-to-left order.
    if get_setting("bubble_order", "ttb") != "ttb":
        from services.bubble_edit import auto_order
        bubbles = auto_order(bubbles, get_setting("bubble_order", "ttb"))

    result = {
        "items": items,
        "lines": grouped_lines,
        "bubbles": bubbles,
        "debug": {
            "slices": slices,
            "marker_records": marker_records,
            "image_scaled": image_scaled,
            "rejected": debug["rejected"],       # readings under the text threshold (page coordinates, scaled image)
            "dropped": dropped,                  # readings / lines / bubbles a later step removed, with the reason
        }
    }
    # text cleanup + skip flags (numbers / symbols) -> result["inpaint_lines"] = lines that really get erased
    apply_bubble_postprocess(result, protected_terms)
    return result
