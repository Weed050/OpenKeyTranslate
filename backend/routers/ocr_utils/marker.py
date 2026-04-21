import re
import cv2

# ---- MARKER CONFIG ----
MARKER_FONT_SCALE = 1.4
MARKER_THICKNESS = 3
MARKER_COLOR = (0, 0, 0)
MARKER_MARGIN_X = 10
MARKER_MARGIN_Y = 10
MARKER_SCORE_THRESHOLD = 0.4


def _make_marker_text(slice_id: int) -> str:
    """Return the canonical marker string for a given slice id."""
    return f"__M{slice_id}__"

def is_any_marker(text: str) -> bool:
    """
    Sprawdza, czy tekst jest dowolnym markerem (np. M0, M1, -M14, __M7__).
    Wykorzystuje istniejącą normalizację, by być odpornym na błędy OCR.
    """
    normalized = _normalize_ocr_text(text)
    # Szukamy wzorca: litera M (lub m) i co najmniej jedna cyfra
    return bool(re.fullmatch(r"M\d+", normalized, re.IGNORECASE))


def _normalize_ocr_text(text: str) -> str:
    """
    Strip noise from OCR text, keeping only alphanumeric characters.

    Removes whitespace and all non-alphanumeric characters so that
    variants like '__M7_', '-M7', '_ M7 __' all normalize to 'M7'.
    """
    t = text.strip()
    t = re.sub(r"[\s]+", "", t)
    t = re.sub(r"[^a-zA-Z0-9]", "", t)
    return t


def _marker_pattern(slice_id: int) -> re.Pattern:
    """
    Build an exact-match regex pattern for the given slice marker.

    Accounts for common OCR digit misreads (e.g. 0 - O, 1 - I).
    Anchored with ^ and $ so that M1 never matches M11.
    """
    digits = str(slice_id)
    digit_alts = {
        "0": "[0Oo]",
        "1": "[1Il]",
        "5": "[5Ss]",
        "8": "[8Bb]",
    }
    pattern_digits = "".join(digit_alts.get(d, d) for d in digits)
    return re.compile(rf"^M{pattern_digits}$", re.IGNORECASE)


def inject_marker(crop, slice_id: int):
    """
    Draw a text marker in the bottom-right corner of a crop for flip detection.

    Renders a white background rectangle behind the marker text to improve
    OCR readability. Modifies the crop in-place.

    :param crop: Image slice as a numpy array (BGR).
    :param slice_id: Index of the current slice, used to build marker text.
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

    x = max(0, w - text_w - MARKER_MARGIN_X)
    y = max(text_h, h - MARKER_MARGIN_Y - baseline)

    cv2.rectangle(
        crop,
        (x - 4, y - text_h - 4),
        (x + text_w + 4, y + baseline + 4),
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

    if best["score"] < MARKER_SCORE_THRESHOLD:
        print(f"[SLICE {slice_id}] marker — low confidence (score={best['score']:.2f}, text={best['text']!r}), skipping")
        return None, False

    print(f"[SLICE {slice_id}] marker — OK (score={best['score']:.2f}, text={best['text']!r})")
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


def detect_flip(marker_item: dict, marker_pos: tuple, slice_shape, tolerance: int = 150) -> bool:
    """
    Determine whether the slice image is flipped 180 degrees.

    Compares the detected marker center with the expected injection position.
    If the distance exceeds tolerance in either axis, a flip is assumed.

    :param marker_item: OCR item containing the detected marker box.
    :param marker_pos: (x, y) pixel position where the marker was injected.
    :param slice_shape: Shape of the slice image (h, w, ...).
    :param tolerance: Maximum allowed pixel distance before flip is declared.
    :return: True if flipped, False otherwise.
    """
    mx, my = marker_pos

    xs = [p[0] for p in marker_item["box"]]
    ys = [p[1] for p in marker_item["box"]]
    cx = sum(xs) / 4
    cy = sum(ys) / 4

    dist_x = abs(cx - mx)
    dist_y = abs(cy - my)

    print(f"[SLICE] marker — expected=({mx:.0f},{my:.0f}) found=({cx:.0f},{cy:.0f}) dist=({dist_x:.0f},{dist_y:.0f})")

    return dist_x > tolerance or dist_y > tolerance


def correct_boxes_180(items: list, slice_shape) -> list:
    """
    Rotate all bounding boxes 180 degrees within the slice dimensions.

    Used to correct OCR box positions after a flip is detected.

    :param items: OCR result items with boxes in local slice coordinates.
    :param slice_shape: Shape of the slice image (h, w, ...).
    :return: Items with corrected bounding boxes.
    """
    h, w = slice_shape[:2]
    for item in items:
        new_box = [[w - x, h - y] for x, y in item["box"]]
        item["box"] = order_box(new_box)
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