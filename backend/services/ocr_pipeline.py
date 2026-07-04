
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
from paddleocr import PaddleOCR

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
from utils.marker import (
    inject_marker, resolve_marker_state, detect_rotation,
    correct_boxes_by_angle, remove_marker, pad_crop_right
)
from utils.text_utils import parse_ocr_results

# Suppress PaddleOCR info and warnings; log errors only
logging.getLogger("ppocr").setLevel(logging.ERROR)


# ---------------- OCR INITIALIZATION ----------------
ocr = PaddleOCR(
    # Specifies the target language model and character dictionary to load (English).
    lang='en',

    # Forces the core pipeline stages (Detection, Classification, and Recognition) onto the GPU via CUDA.
    device='gpu',

    # Activates the text orientation classifier (CLS model). It checks whether text boxes are upside
    # down or rotated sideways (0, 90, 180, 270 degrees) and automatically flips them upright before recognition.
    use_textline_orientation=True,

    # Disables Intel MKLDNN (oneDNN) acceleration. Since MKLDNN is a mathematics kernel library optimized
    # strictly for Intel CPUs, it must be False when targeting native GPU processing to avoid conflicts.
    # enable_mkldnn=True,
    enable_mkldnn=False,

    # Disables geometric text-unwarping networks. Unwarping attempts to digitally flatten curved or bent pages
    # from physical book photos; disabling this bypasses an expensive model unneeded for flat digital scans/webtoons.
    use_doc_unwarping=False,

    # Defines how many cropped text-line bounding boxes are bundled together and evaluated by the GPU
    # concurrently. Increasing this maximizes GPU core utilization and speeds up processing, but increases VRAM use.
    # text_recognition_batch_size=64,
)



# ---------------- CORE FUNCTIONS ----------------

# ---------------- SCALE BACK BOXES ----------------
def to_original_coords(box):
    """Scale box coordinates back to original image size."""
    return [[x / SCALE, y / SCALE] for x, y in box]


# ---------------- OVERLAP H & RATIO ----------------
def get_overlap_size(image_height):
    """

    Define slice height and overlap for OCR slicing.

    Uses discrete heuristics based on image size to ensure stable OCR
    behavior across small, medium, and large images.

    TODO: Replace this function with a dynamic method for calculating slice height and overlap size,
     or fine-tune these heuristic thresholds for better optimization.

    :return: slice_h - slice height, overlap - overlap height (how much 2 slices need to overlap)
    """

    # --- Very small images ---
    if image_height < 1100 * SCALE:
    # if image_height < 600:
        return image_height, 0

    # --- Small / medium ---
    elif image_height < 1600  * SCALE:
        slice_h = int(image_height * 0.6)

    # --- Medium ---
    elif image_height < 3000 * SCALE:
        slice_h = int(image_height * 0.4)

    # --- Large ---
    else:
        slice_h = int(image_height * 0.15)

    # Clamp slice height to reasonable limits
    slice_h = max(250, min(slice_h, 1400))

    # Calculate overlap size using a bounded percentage calculation
    overlap = int(slice_h * 0.15)
    overlap = min(max(overlap, 40), 160)

    return slice_h, overlap


# ---------------- SLICING, OCR ----------------
def run_ocr_sliced(image: np.ndarray) -> tuple[list[dict], list[tuple[int, int]], list[dict]]:
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
        1. Image Slicing: Splits the image into horizontal slices with a dynamic overlap
           to prevent text from being vertically clipped at boundaries.
        2. Canvas Padding: If WHITE_SPACE is enabled, appends a temporary blank margin to
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

    :return:
    tuple: A structural tuple containing:
    - list[dict]: Adjusted OCR text items (text, confidence score, global box, slice_id).
    - list[tuple[int, int]]: Slice boundaries mapped as (y_start, y_end) coordinates.
    - list[dict]: Marker state debug records used for rotation and alignment validation.
    """

    h, w = image.shape[:2]

    slice_h, overlap = get_overlap_size(h)

    all_items = []
    y = 0
    slice_id = 0
    slices = []
    marker_records = []

    while y < h:
        y_end = min(y + slice_h, h)
        slices.append((y, y_end))

        # Crop current slice from the image
        crop = image[y:y_end, :].copy()
        crop_original = crop.copy()
        content_w = crop.shape[1]
        pad_width = WHITE_SPACE_PAD_WIDTH if WHITE_SPACE else 0

        # 1. Prepare slice canvas and inject tracking marker
        if WHITE_SPACE:
            ocr_crop = pad_crop_right(crop_original, pad_width)
            ocr_crop, _, marker_pos = inject_marker(
                ocr_crop, slice_id, content_width=content_w
            )
        else:
            ocr_crop, _, marker_pos = inject_marker(crop_original.copy(), slice_id)

        print(f"\n[SLICE {slice_id}] {y}:{y_end}")

        # 2. Execute OCR engine (performed on ocr_crop sliced image)
        result = ocr.predict(ocr_crop)
        slice_items = parse_ocr_results(result, slice_id, TEXT_THRESHOLD)
        ocr_shape = ocr_crop.shape[:2]

        # 3. Initial filter to extract text items without the marker
        # marker_item, found = find_marker(slice_items, slice_id, marker_pos)
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

        # 6. Logging, data cleanup and boundary clipping
        marker_records.append(make_marker_record(slice_id, y, marker_pos, marker_item, angle))

        slice_items = remove_marker(slice_items, slice_id)

        # Clip X coordinates to content width to discard tracking padding area
        for item in slice_items:
            item["box"] = [
                [min(max(0.0, float(px)), float(content_w)), float(py)]
                for px, py in item["box"]
            ]

        # 7. Map coordinates from localized slice space to global page coordinates
        for item in slice_items:
            shifted_box = [[x, y0 + y] for x, y0 in item["box"]]
            item["box"] = shifted_box

        all_items.extend(slice_items)

        # 8. Advance window to the next slice step
        y += slice_h - overlap
        slice_id += 1

    return all_items, slices, marker_records



# =====================================================================
# THE BLACK BOX
# =====================================================================

def process_image(image_bgr: np.ndarray) -> dict:
    """
    Main entry point for the OCR module.
    Accepts an image, processes it entirely, and returns structured data.
    """
    image_scaled = cv2.resize(image_bgr, None, fx=SCALE, fy=SCALE)
    image_width = image_scaled.shape[1]
    _, overlap = get_overlap_size(image_scaled.shape[0])

    print("Running OCR Pipeline...\n")
    items, slices, marker_records = run_ocr_sliced(image_scaled)

    items = remove_slice_boundary_duplicates(items, max_y_jitter=int(overlap * 1.5))
    grouped_lines = build_text_lines(items, image_width=image_width)

    print("\n=== POST-PROCESSING: PAGE-LEVEL DEDUPLICATION ===")
    items = smart_deduplicate_by_lines(
        grouped_lines, items, image_scaled, ocr,
        word_thresh=DEDUP_WORD_THRESH,
        geo_thresh=DEDUP_GEO_THRESH,
        min_overlap_area=DEDUP_MIN_OVERLAP_PX
    )

    grouped_lines = build_text_lines(items, image_width=image_width)
    grouped_lines = filter_noise_lines(grouped_lines)
    bubbles = group_lines_into_bubbles(grouped_lines)
    bubbles = filter_noise_bubbles(bubbles)

    return {
        "items": items,
        "lines": grouped_lines,
        "bubbles": bubbles,
        "debug": {
            "slices": slices,
            "marker_records": marker_records,
            "image_scaled": image_scaled
        }
    }
