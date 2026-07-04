
# marker.py

"""
Provides robust utilities for injecting, tracking, and recovering OCR alignment markers.

This module mitigates unpredictable spatial rotations introduced by the OCR engine
(due to `use_textline_orientation=True`). By injecting and tracking a fixed anchor
marker, we can calculate and revert any coordinate shifts before downstream processes
like text deduplication and inpainting are affected.

The Marker Pipeline:
1. Padding & Injection: A white "safe zone" is artificially padded to the right edge
   of the image slice to avoid covering manga art. A highly specific slice-aware marker
   (e.g., `__M1__`) is then injected into this space.
2. Threshold Bypass & Parsing: Because the marker is an artificial element, the OCR
   engine often assigns it a very low confidence score. During parsing, marker strings
   explicitly bypass standard confidence and linguistic plausibility thresholds.
   Regex rules also gracefully handle common OCR visual misreads (e.g., 1 <-> I, 0 <-> O).
3. Rotation Detection: The detected marker's bounding box is compared against its
   original injected coordinates to deduce and reverse any applied spatial rotation.
4. Aggressive Recovery (Re-shot): If a marker is lost or accidentally merged with
   nearby story text, the module dynamically relocates the marker to a new,
   collision-free spot and forces a localized OCR re-execution to rescue the pipeline.
"""

import re
import cv2
import numpy as np

# Importing marker configuration directly from the config file
from core.config import (
    MARKER_FONT_SCALE, MARKER_THICKNESS, MARKER_COLOR,
    MARKER_MARGIN_X, MARKER_MARGIN_Y, MARKER_SCORE_THRESHOLD
)

from utils.text_utils import _normalize_ocr_text, parse_ocr_results, marker_pattern

def _make_marker_text(slice_id: int) -> str:
    """Return the canonical marker string for a given slice id."""
    return f"__M{slice_id}__"

def _marker_pattern(slice_id: int) -> re.Pattern:
    """
    Build an exact-match regex pattern for the given slice marker.

    Accounts for common OCR digit misreads (e.g. 0 - O, 1 - I).
    Anchored with ^ and $ so that M1 never matches M11.
    """
    return marker_pattern(slice_id, anchored=True)


def pad_crop_right(crop, pad_width: int):
    """Add a white strip on the right for marker placement without covering manga text."""
    return cv2.copyMakeBorder(
        crop, 0, 0, 0, pad_width, cv2.BORDER_CONSTANT, value=[255, 255, 255]
    )


def inject_marker(crop, slice_id: int, marker_pos_override: tuple = None, content_width: int = None):
    """
    Draw a text marker in the bottom-right corner of a crop for flip detection.

    Renders a white background rectangle behind the marker text to improve
    OCR readability. Modifies the crop in-place.

    :param crop: Image slice as a numpy array (BGR).
    :param slice_id: Index of the current slice, used to build marker text.
    :param marker_pos_override: If provided, inject at this (x, y) instead of bottom-right.
    :param content_width: When set (WHITE_SPACE padding), place the marker in the right
        margin strip [content_width .. crop.width], not on top of page content.
    :return: Tuple of (modified crop, marker text, (x, y) position).
    """
    marker_text = _make_marker_text(slice_id)
    h, w = crop.shape[:2]

    (text_w, text_h), baseline = cv2.getTextSize(
        marker_text,
        cv2.FONT_HERSHEY_SIMPLEX,
        MARKER_FONT_SCALE,
        MARKER_THICKNESS,
    )

    if marker_pos_override is not None:
        x, y = marker_pos_override
    elif content_width is not None:
        pad_w = max(1, w - content_width)
        x = content_width + max(8, (pad_w - text_w) // 2)
        y = max(text_h, h - MARKER_MARGIN_Y - baseline)
    else:
        x = max(0, w - text_w - MARKER_MARGIN_X)
        y = max(text_h, h - MARKER_MARGIN_Y - baseline)

    BUFFER = 8

    cv2.rectangle(
        crop,
        (x - BUFFER, y - text_h - BUFFER),
        (x + text_w + BUFFER, y + baseline + BUFFER),
        (255, 255, 255),
        thickness=-1,
    )
    cv2.putText(
        crop,
        marker_text,
        (x, y),
        cv2.FONT_HERSHEY_SIMPLEX,
        MARKER_FONT_SCALE,
        MARKER_COLOR,
        MARKER_THICKNESS,
    )

    return crop, marker_text, (x, y)


def find_marker(items: list, slice_id: int, marker_pos: tuple) -> tuple[dict | None, bool]:
    """
    Search OCR items for the marker belonging to the given slice.

    First searches near the injected marker position, then falls back to
    the full item list. Uses exact-id regex to avoid cross-slice matches.

    :param items: OCR result items for the current slice.
    :param slice_id: Index of the current slice.
    :param marker_pos: (x, y) pixel position where the marker was injected.
    :return: Tuple of (marker item or None, found bool).
    """
    pattern = _marker_pattern(slice_id)
    mx, my = marker_pos

    TOLERANCE = 80
    nearby = [
        item for item in items
        if abs(item["box"][0][0] - mx) < TOLERANCE and abs(item["box"][0][1] - my) < TOLERANCE
    ]

    search_pool = nearby if nearby else items
    candidates = [
        item for item in search_pool
        if pattern.search(_normalize_ocr_text(item["text"]))
    ]

    if not candidates:
        _log_marker_miss(items, slice_id)
        return None, False

    best = max(candidates, key=lambda i: i["score"])

    # Normalize OCR misreads before extracting digits (same substitutions as _marker_pattern)
    _ocr_corrected = (
        _normalize_ocr_text(best["text"])
        .replace("O", "0").replace("o", "0")
        .replace("I", "1").replace("l", "1")
        .replace("S", "5")
        .replace("B", "8")
    )

    detected_id = "".join(c for c in _ocr_corrected if c.isdigit())

    if best["score"] < MARKER_SCORE_THRESHOLD:
        print(
            f"[SLICE {slice_id}] marker — low confidence ({best['score']:.2f}), text={best['text']!r}, ID={detected_id}")
        return None, False

    print(f"[SLICE {slice_id}] marker — OK (score={best['score']:.2f}, text={best['text']!r}, ID={detected_id})")
    return best, True


def _log_marker_miss(items: list, slice_id: int):
    """
    Log a diagnostic message when the marker was not found in OCR results.

    Prints the bottom-most OCR items to help identify why detection failed.

    :param items: All OCR items from the slice.
    :param slice_id: Index of the current slice.
    """
    if not items:
        print(f"[SLICE {slice_id}] marker — NOT found (slice has no OCR items)")
        return

    bottom_items = sorted(items, key=lambda i: i["box"][0][1], reverse=True)[:5]
    candidates_str = ", ".join(
        f"{it['text']!r}(score={it['score']:.2f})" for it in bottom_items
    )
    print(f"[SLICE {slice_id}] marker — NOT found (bottom items: [{candidates_str}])")


def remove_marker(items: list, slice_id: int) -> list:
    """
    Remove the marker item for the given slice from the item list.

    Uses the same exact-id pattern as find_marker to avoid false removals.

    :param items: OCR result items for the current slice.
    :param slice_id: Index of the current slice.
    :return: Filtered list with marker item removed.
    """
    pattern = _marker_pattern(slice_id)
    return [
        item for item in items
        if not pattern.search(_normalize_ocr_text(item["text"]))
    ]


def order_box(box: list) -> list:
    """
    Reorder a quadrilateral box to canonical [top-left, top-right, bottom-right, bottom-left].

    :param box: List of four [x, y] points in any order.
    :return: Reordered list of four [x, y] points.
    """
    box = sorted(box, key=lambda p: (p[1], p[0]))
    top = sorted(box[:2], key=lambda p: p[0])
    bottom = sorted(box[2:], key=lambda p: p[0])
    return [top[0], top[1], bottom[1], bottom[0]]


def detect_rotation(marker_item: dict, marker_pos: tuple, slice_shape, content_width: int = None) -> int:
    """
    Infer slice rotation from where OCR found the marker vs where it was originally injected.

    :param slice_shape: Must match the image OCR ran on (including right padding when used).
    :param content_width: The manga/content width before padding; utilized strictly for diagnostics.
    :return: Inferred rotation angle (0, 90, 180, or 270).
    """
    h, w = slice_shape[:2]
    mx, my = marker_pos

    box = marker_item["box"]
    cx_detected = sum(p[0] for p in box) / 4
    cy_detected = sum(p[1] for p in box) / 4

    # OCR polygon centroid can land on body text (merged reads, wrong box). If it is
    # far from the injected marker, do not rotate — false 180° was shifting all boxes left.
    # MARKER_POS_TOLERANCE = 120
    # inject_dist = np.sqrt((cx_detected - mx) ** 2 + (cy_detected - my) ** 2)
    # if inject_dist > MARKER_POS_TOLERANCE:
    #     print(
    #         f"[ROTATION] skipped — marker box ({cx_detected:.1f}, {cy_detected:.1f}) "
    #         f"is {inject_dist:.0f}px from injected {marker_pos} (>{MARKER_POS_TOLERANCE}px)"
    #     )
    #     return 0

    print(f"[ROTATION DEBUG] expected={marker_pos}, detected=({cx_detected:.1f}, {cy_detected:.1f})")
    if content_width is not None:
        print(f"[ROTATION DEBUG] content_width={content_width}, ocr_width={w}")

    # Instead of looking for rigid corners, dynamically calculate where the marker SHOULD land
    # depending on how PaddleOCR rotated the coordinate spatial system.
    expected_positions = {
        0:   (mx, my),          # No rotation
        180: (w - mx, h - my),  # 180-degree rotation (e.g., Slice 2)
        90:  (h - my, mx),      # 90-degree rotation (dimensions swapped)
        270: (my, w - mx),      # 270-degree rotation (e.g., Slice 4, 7, 10)
    }

    ROTATION_THRESHOLD = 200
    best_angle = 0
    min_dist = float('inf')

    for angle, (exp_x, exp_y) in expected_positions.items():
        dist = np.sqrt((cx_detected - exp_x) ** 2 + (cy_detected - exp_y) ** 2)
        if dist < min_dist:
            min_dist = dist
            best_angle = angle

    if min_dist < ROTATION_THRESHOLD:
        status = "OK" if best_angle == 0 else f"ROTATED {best_angle}°"
        print(f"[ROTATION] - {status} - (dist={min_dist:.1f}px)")
        return best_angle

    print(f"[ROTATION] - UNKNOWN - (dist={min_dist:.1f}px, marker found in no man's land)")
    return 0

def correct_boxes_by_angle(items: list, angle: int, slice_shape) -> list:
    """
    Rotate all bounding boxes by the specified angle within the slice dimensions.

    Used to correct OCR box positions after a rotation is detected.

    :param items: OCR result items with boxes in local slice coordinates.
    :param angle: Rotation angle (0, 90, 180, or 270).
    :param slice_shape: Shape of the slice image (h, w, ...).
    :return: Items with corrected bounding boxes.
    """
    if angle == 0:
        return items

    h, w = slice_shape[:2]
    for item in items:
        new_box = []
        for x, y in item["box"]:
            if angle == 180:
                new_box.append([w - x, h - y])
            elif angle == 90:
                new_box.append([y, h - x])
            elif angle == 270:
                new_box.append([w - y, x])
        item["box"] = new_box
    return items


def slice_has_meaningful_text(slice_items: list, slice_id: int, threshold: float) -> bool:
    """
    Check whether a slice contains at least one high-confidence OCR result.

    Excludes the marker item from the check to avoid false positives.

    :param slice_items: OCR result items for the current slice.
    :param slice_id: Index of the current slice (used to identify marker).
    :param threshold: Minimum score to consider text meaningful.
    :return: True if at least one non-marker item meets the threshold.
    """
    pattern = _marker_pattern(slice_id)
    return any(
        item["score"] >= threshold
        for item in slice_items
        if not pattern.search(_normalize_ocr_text(item["text"]))
    )


def boxes_overlap(box1, box2):
    """
    Calculate the overlap between two bounding boxes.

    :param box1: Polygon [[x,y], [x,y], [x,y], [x,y]]
    :param box2: Polygon [[x,y], [x,y], [x,y], [x,y]]
    :return: (inter_area, overlap_ratio_of_smaller_box)
    """

    def to_rect(box):
        pts = np.array(box)
        return pts[:, 0].min(), pts[:, 1].min(), pts[:, 0].max(), pts[:, 1].max()

    x1a, y1a, x2a, y2a = to_rect(box1)
    x1b, y1b, x2b, y2b = to_rect(box2)

    xi1, yi1 = max(x1a, x1b), max(y1a, y1b)
    xi2, yi2 = min(x2a, x2b), min(y2a, y2b)

    inter = max(0, xi2 - xi1) * max(0, yi2 - yi1)

    area_a = float(x2a - x1a) * float(y2a - y1a)
    area_b = float(x2b - x1b) * float(y2b - y1b)
    smaller = min(area_a, area_b)

    overlap_ratio = (inter / smaller) if smaller > 0 else 0.0

    return inter, overlap_ratio


def marker_overlaps_text(marker_item: dict, text_items: list, overlap_threshold: float = 0.1) -> bool:
    """
    Check if marker bounding box overlaps with any text boxes.

    :param marker_item: OCR item with marker's box
    :param text_items: List of OCR items (non-marker text)
    :param overlap_threshold: Min overlap ratio to consider as problematic
    :return: True if marker overlaps with any text
    """
    for item in text_items:
        _, ratio = boxes_overlap(marker_item["box"], item["box"])
        if ratio > overlap_threshold:
            print(f"[MARKER OVERLAP] marker overlaps '{item['text']}' (ratio={ratio:.2f})")
            return True
    return False

def find_free_spot_bottom_right(
        crop_h, crop_w, text_items,
        marker_text_size,
        extra_no_go_box=None,
        margin_x=40,
        margin_y=40,
        min_x=0,
) -> tuple | None:

    """
    Find a free collisionless spot to safely inject the marker in the lower portion of the image.
    """

    text_w, text_h = marker_text_size
    pad = 8

    def make_marker_box(x, y):
        return [[x - pad, y - text_h - pad], [x + text_w + pad, y - text_h - pad],
                [x + text_w + pad, y + pad], [x - pad, y + pad]]

    def overlaps_any(box, obstacles):
        for obs in obstacles:
            _, ratio = boxes_overlap(box, obs)
            if ratio > 0.05:
                return True
        return False

    no_go = [item["box"] for item in text_items]
    if extra_no_go_box is not None:
        no_go.append(extra_no_go_box)

    # Search higher up — in a practical text baseline lane.
    # Instead of searching directly from the absolute bottom, scan from 2/3 height down.
    search_start = int(crop_h * 0.7)   # Centered roughly in the lower half
    search_end = int(crop_h * 0.15)    # Scan up to ~1/6 height (safe margin from the top)

    step = 20

    print(f"[MARKER RELOCATION DEBUG] search range: y={search_start} to y={search_end}, crop_h={crop_h}")

    right_start = max(min_x, crop_w - text_w - margin_x)

    # First, attempt to position on the RIGHT side (within the padding band when min_x > 0)
    for y_try in range(search_start, search_end, -step):
        for x_try in range(right_start, max(min_x, crop_w // 2) - 1, -step):
            # If the marker overflows the right edge, shift it back to the left
            if x_try + text_w + pad > crop_w:
                x_try = crop_w - text_w - pad - 2

            candidate_box = make_marker_box(x_try, y_try)
            if not overlaps_any(candidate_box, no_go):
                print(f"[MARKER RELOCATION][OK]  Free spot discovered at RIGHT: ({x_try}, {y_try})")
                return x_try, y_try

    # If no available spot was discovered on the right, attempt on the LEFT side
    for y_try in range(search_start, search_end, -step):
        for x_try in range(margin_x, crop_w // 2, step):
            candidate_box = make_marker_box(x_try, y_try)
            if not overlaps_any(candidate_box, no_go):
                print(f"[MARKER RELOCATION][OK]  Free spot discovered at LEFT: ({x_try}, {y_try})")
                return x_try, y_try

    print(f"[MARKER RELOCATION][X]  No free spot found within safe range parameters")
    return None

def recover_merged_marker(items: list, slice_id: int) -> dict | None:
    """
    Fallback recovery: searches for a marker string that was accidentally merged into the
    surrounding manga text stream by the OCR engine (e.g., 'THERE WAS A SWOI  M2..').
    """
    pattern = marker_pattern(slice_id, anchored=False)

    for item in items:
        normalized = _normalize_ocr_text(item["text"])
        if pattern.search(normalized):
            print(f"[SLICE {slice_id}] RECOVERED merged marker within text stream: {item['text']!r}")
            return item

    return None




def get_marker_size(slice_id: int) -> tuple:
    """
    Retrieve the spatial dimensions (width, height) of the marker for a given identifier.
    """
    text = _make_marker_text(slice_id)
    (w, h), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, MARKER_FONT_SCALE, MARKER_THICKNESS)
    return w, h

def resolve_marker_state(
    ocr,
    crop_original,
    ocr_crop,
    slice_items,
    slice_id,
    marker_pos,
    text_items,
    text_threshold,
    pad_width: int = 0,
):
    """
    State controller managing marker evaluation, rescue operations, and relocation workflows.

    Note: ocr_crop represents the padded image upon which OCR executed
    (sharing the identical coordinate space as slice_items).
    """

    marker_item, found = find_marker(slice_items, slice_id, marker_pos)
    is_merged = False
    content_w = crop_original.shape[1]

    # --- GATE 0: Completely empty crop (no manga text detected) ---
    if len(text_items) == 0:
        if found:
            print(f"[RESOLVER][OK] GATE 0: Blank slice. Marker found. Fast exit.")
        else:
            print(
                f"[RESOLVER][SKIP >>] GATE 0: Blank slice and marker missing. Skipping recovery (no text to rotate anyway).")
        return marker_item, found, marker_pos, ocr_crop, slice_items

    # --- GATE 1: Initial state evaluation ---
    if found:
        overlaps = marker_overlaps_text(marker_item, text_items, overlap_threshold=0.15)
        if not overlaps:
            print(f"[RESOLVER][OK] GATE 1: Success - Marker found and clean (no text overlap).")
            return marker_item, found, marker_pos, ocr_crop, slice_items
        else:
            print(f"[RESOLVER][!] GATE 1: Warning - Marker found, but it overlaps with manga text.")
    else:
        print(f"[RESOLVER][X] GATE 1: Failed - Marker completely missing in initial OCR pass.")

    # --- GATE 2: Recovering merged marker text strings ---
    if not found:
        print(f"[RESOLVER] → GATE 2: Attempting to recover merged marker from text...")
        marker_item = recover_merged_marker(slice_items, slice_id)
        if marker_item:
            found = True
            is_merged = True
            slice_items = remove_marker(slice_items, slice_id)
            text_items = remove_marker(slice_items, slice_id)
            print(
                f"[RESOLVER][OK] GATE 2: Rescue successful - Merged marker recovered from text: '{marker_item['text']}'")
        else:
            print(f"[RESOLVER][X] GATE 2: Rescue failed - No merged marker found in text.")

    # --- GATE 3: Relocation protocol and OCR re-execution ---
    needs_relocation = (
            not found or
            is_merged or
            (found and marker_overlaps_text(marker_item, text_items))
    )

    if needs_relocation:
        reason = "missing" if not found else ("merged into text" if is_merged else "overlapping text")
        print(f"[RESOLVER] -> GATE 3: Relocation required because marker is {reason}.")
        m_w, m_h = get_marker_size(slice_id)

        print(f"[RESOLVER]    Text items in slice: {len(text_items)}")
        for ti in text_items[:3]:
            print(f"[RESOLVER]      - '{ti['text']}' at y={ti['box'][0][1]:.0f}")

        extra_no_go = None
        if not found:
            mx, my = marker_pos
            pad = 8
            extra_no_go = [[mx - pad, my - m_h - pad], [mx + m_w + pad, my - m_h - pad],
                           [mx + m_w + pad, my + pad], [mx - pad, my + pad]]
            print(f"[RESOLVER]  Extra no-go zone set at default position: x={mx}, y={my}")

        search_h, search_w = ocr_crop.shape[0], ocr_crop.shape[1]
        new_pos = find_free_spot_bottom_right(
            search_h, search_w,
            text_items,
            (m_w, m_h),
            extra_no_go_box=extra_no_go,
            min_x=content_w if pad_width else 0,
        )

        if new_pos:
            print(f"[RESOLVER][OK] GATE 3: New free spot found at {new_pos}. Re-injecting marker and re-running OCR...")
            crop = crop_original.copy()
            cw = content_w if pad_width else None

            if pad_width:
                crop = pad_crop_right(crop, pad_width)

            crop, _, marker_pos = inject_marker(
                crop, slice_id, marker_pos_override=new_pos, content_width=cw
            )

            result = ocr.predict(crop)
            slice_items = parse_ocr_results(result, slice_id, text_threshold)
            marker_item, found = find_marker(slice_items, slice_id, marker_pos)

            if found:
                print(f"[RESOLVER][OK] SUCCESS after re-shot: Marker found at new position: '{marker_item['text']}'")
            else:
                print(
                    f"[RESOLVER][!] WARNING after re-shot: Marker STILL NOT found at new position. Trying fallback recovery...")
                marker_item = recover_merged_marker(slice_items, slice_id)
                if marker_item:
                    found = True
                    print(f"[RESOLVER][OK] FALLBACK SUCCESS: Found merged marker after re-shot: '{marker_item['text']}'")
                else:
                    print(f"[RESOLVER][X] TOTAL FAILURE: Marker completely lost after re-shot.")

            return marker_item, found, marker_pos, crop, slice_items
        else:
            print(f"[RESOLVER][X] GATE 3: No free spot found in slice. Returning original state.")

    return marker_item, found, marker_pos, ocr_crop, slice_items