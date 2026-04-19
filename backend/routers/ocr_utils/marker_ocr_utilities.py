import re
import cv2

# ---- MARKER CONFIG ----
MARKER_FONT_SCALE = 1.4
MARKER_THICKNESS = 3
MARKER_COLOR = (0, 0, 0)
MARKER_MARGIN_X = 10   # od prawej krawędzi slica
MARKER_MARGIN_Y = 10   # od dolnej krawędzi slica
MARKER_SCORE_THRESHOLD = 0.4


def _make_marker_text(slice_id: int) -> str:
    """Canonical marker string."""
    return f"__M{slice_id}__"


def _normalize_ocr_text(text: str) -> str:
    t = text.strip()
    t = re.sub(r"[\s]+", "", t)
    t = re.sub(r"[^a-zA-Z0-9]", "", t)  # zostaw tylko litery i cyfry
    return t


def _marker_pattern(slice_id: int) -> re.Pattern:
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
    Inject a visible text marker in the bottom-right corner of a crop.
    Returns (crop_with_marker, marker_text, position).
    """
    marker_text = _make_marker_text(slice_id)
    h, w = crop.shape[:2]

    # oszacuj szerokość tekstu żeby nie wychodzić poza krawędź
    (text_w, text_h), baseline = cv2.getTextSize(
        marker_text,
        cv2.FONT_HERSHEY_SIMPLEX,
        MARKER_FONT_SCALE,
        MARKER_THICKNESS,
    )

    x = max(0, w - text_w - MARKER_MARGIN_X)
    y = max(text_h, h - MARKER_MARGIN_Y - baseline)

    # białe tło pod tekstem — lepsza czytelność dla OCR
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
    Search OCR items for the marker belonging to `slice_id`.
    Uses injected position (marker_pos) to narrow search area first,
    then falls back to full-list text search.
    """
    pattern = _marker_pattern(slice_id)
    mx, my = marker_pos  # dokładna pozycja gdzie marker został narysowany

    # szukaj najpierw w okolicy markera (tolerancja ±80px)
    TOLERANCE = 80
    nearby = [
        item for item in items
        if abs(item["box"][0][0] - mx) < TOLERANCE and abs(item["box"][0][1] - my) < TOLERANCE
    ]

    candidates = []
    search_pool = nearby if nearby else items  # fallback na całą listę

    for item in search_pool:
        normalized = _normalize_ocr_text(item["text"])
        if pattern.search(normalized):
            candidates.append(item)

    if not candidates:
        _log_marker_miss(items, slice_id)
        return None, False

    best = max(candidates, key=lambda i: i["score"])

    if best["score"] < MARKER_SCORE_THRESHOLD:
        print(f"[SLICE {slice_id}] marker found but low confidence "
              f"(score={best['score']:.2f}, text={best['text']!r}) — treating as NOT found")
        return None, False

    print(f"[SLICE {slice_id}] marker OK (score={best['score']:.2f}, text={best['text']!r})")
    return best, True


def _log_marker_miss(items: list, slice_id: int):
    """Print what OCR detected near bottom-right — helps diagnose marker failures."""
    # bierzemy 5 itemów z najwyższym Y (dół slica)
    if not items:
        print(f"[SLICE {slice_id}] marker NOT found — no OCR items at all in this slice")
        return

    bottom_items = sorted(items, key=lambda i: i["box"][0][1], reverse=True)[:5]
    candidates_str = ", ".join(
        f"{it['text']!r}(score={it['score']:.2f})" for it in bottom_items
    )
    print(
        f"[SLICE {slice_id}] marker NOT found — "
        f"bottom items: [{candidates_str}]"
    )


def remove_marker(items: list, slice_id: int) -> list:
    """
    Remove marker item for `slice_id` from items list.
    Uses same exact-id pattern as find_marker.
    """
    pattern = _marker_pattern(slice_id)
    return [
        item for item in items
        if not pattern.search(_normalize_ocr_text(item["text"]))
    ]

def order_box(box):
    box = sorted(box, key=lambda p: (p[1], p[0]))
    top = sorted(box[:2], key=lambda p: p[0])
    bottom = sorted(box[2:], key=lambda p: p[0])
    return [top[0], top[1], bottom[1], bottom[0]]


def detect_flip(marker_item, marker_pos: tuple, slice_shape, tolerance: int = 150) -> bool:
    """
    Porównuje znalezioną pozycję markera z oczekiwaną (marker_pos).
    Jeśli marker jest daleko od miejsca gdzie go wstawiliśmy → flip.
    """
    mx, my = marker_pos  # gdzie narysowaliśmy marker (prawy dół)

    xs = [p[0] for p in marker_item["box"]]
    ys = [p[1] for p in marker_item["box"]]
    cx = sum(xs) / 4
    cy = sum(ys) / 4

    dist_x = abs(cx - mx)
    dist_y = abs(cy - my)

    print(f"         marker expected=({mx:.0f},{my:.0f}) found=({cx:.0f},{cy:.0f}) dist=({dist_x:.0f},{dist_y:.0f})")

    return dist_x > tolerance or dist_y > tolerance


def correct_boxes_180(items: list, slice_shape) -> list:
    h, w = slice_shape[:2]
    for item in items:
        new_box = [[w - x, h - y] for x, y in item["box"]]
        item["box"] = order_box(new_box)
    return items

def slice_has_meaningful_text(slice_items: list, slice_id: int, threshold: float) -> bool:
    """Czy slice zawiera choć jeden item z score >= threshold, pomijając marker."""
    pattern = _marker_pattern(slice_id)
    return any(
        item["score"] >= threshold
        for item in slice_items
        if not pattern.search(_normalize_ocr_text(item["text"]))
    )