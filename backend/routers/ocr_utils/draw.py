import cv2
import numpy as np

# draw.py

def draw_text_boxes(items, image, to_original_coords):
    '''
    Visualize OCR results on an image.

    Draws bounding boxes and text labels using coordinates from OCR items.
    Uses `to_original_coords` to map boxes back to original image scale.
    :param items: items returned from ocr
    :param image: image in original scale
    :param to_original_coords: parameter: box = [[x1,y1], [x2,y2], ...]
    :return: image -
    '''
    for item in items:
        orig_box = to_original_coords(item["box"])
        pts = np.array(orig_box, np.int32).reshape((-1, 1, 2))

        cv2.polylines(image, [pts], True, (0, 0, 255), 2)

        x, y = orig_box[0]
        cv2.putText(
            image,
            item["text"],
            (int(x), int(y) - 5),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            (0, 0, 255),
            2
        )
    return image

def print_ocr_items_grouped(items, to_original_coords):
    print("\n--- OCR RESULTS ---")

    # grupowanie
    slices_map = {}
    for item in items:
        sid = item.get("slice_id", -1)
        slices_map.setdefault(sid, []).append(item)

    for sid in sorted(slices_map.keys()):
        print(f"\n[S{sid}]")

        # sortowanie po Y (czytelność jak tekst)
        slice_items = slices_map[sid]
        slice_items.sort(key=lambda x: to_original_coords(x["box"])[0][1])

        for item in slice_items:
            box = to_original_coords(item["box"])
            x, y = box[0]

            print(f"   {item['text']:<15} | score={item['score']:.2f} ({int(x)}, {int(y)})")


def draw_slices(image, slices, scale):
    """
    Draw slice rectangles with different colors for debugging.
    """

    h, w = image.shape[:2]

    colors = [
        (255, 0, 0),    # niebieski
        (0, 255, 0),    # zielony
        (0, 0, 255),    # czerwony
        (255, 255, 0),  # cyan
        (255, 0, 255),  # magenta
        (0, 255, 255),  # żółty
    ]

    for i, (y1, y2) in enumerate(slices):
        color = colors[i % len(colors)]

        y1_orig = int(y1 / scale)
        y2_orig = int(y2 / scale)

        cv2.rectangle(
            image,
            (0, y1_orig),
            (w, y2_orig),
            color,
            2
        )

        # podpis
        cv2.putText(
            image,
            f"S{i}",
            (10, y1_orig + 25),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            color,
            2
        )

    return image


import cv2
import numpy as np


def draw_merged_lines(image, grouped_lines, scale_fn, color=(0, 255, 0), thickness=3):
    """
    Rysuje zielone ramki wokół pogrupowanych linii tekstu dla celów debugowania.
    :param image: Obraz, na którym rysujemy (oryginalny)
    :param grouped_lines: Lista słowników z pogrupowanymi liniami (wynik build_text_lines)
    :param scale_fn: Funkcja to_original_coords do przeliczania skali
    :param color: Kolor BGR (domyślnie zielony)
    :param thickness: Grubość linii
    """
    overlay = image.copy()

    for g_line in grouped_lines:
        # Przeliczamy współrzędne na oryginalne
        orig_box = scale_fn(g_line["box"])

        # Przygotowujemy punkty dla OpenCV (zakładamy prostokąt z box_stats)
        # Format Paddle to [[x1,y1], [x2,y1], [x2,y2], [x1,y2]]
        pts = np.array(orig_box, dtype=np.int32)

        # Rysujemy kontur (polylines obsłuży ewentualne pochylone ramki)
        cv2.polylines(image, [pts], isClosed=True, color=color, thickness=thickness)

        # Opcjonalnie: Dodaj mały tekst z ID linii nad ramką
        x1, y1 = pts[0]
        cv2.putText(image, g_line["line_id"], (x1, y1 - 10),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)

    return image


def print_merged_lines(grouped_lines):
    """
    Wypisuje pogrupowane linie tekstu w czytelny sposób w konsoli.
    """
    print("\n" + "=" * 50)
    print(f"{'ID LINII':<10} | {'TEKST':<40} | {'SCORE':<5}")
    print("-" * 50)

    for line in grouped_lines:
        line_id = line.get("line_id", "N/A")
        text = line.get("text", "")
        score = line.get("score", 0.0)

        # Opcjonalnie: możemy też pokazać ile słów składa się na linię
        word_count = len(line.get("word_ids", []))

        print(f"{line_id:<10} | {text:<40} | {score:.2f} ({word_count} words)")

    print("=" * 50 + "\n")


def print_detected_bubbles(bubbles):
    """Wypisuje sformatowane dymki w konsoli."""
    print("\n" + "═" * 60)
    print(f"║ {'ID DYMKU':<12} ║ {'ZAWARTOŚĆ TEKSTOWA':<43} ║")
    print("╠" + "═" * 14 + "╬" + "═" * 44 + "╣")

    # for b in bubbles:
    #     # Skracanie tekstu do konsoli, jeśli jest za długi
    #     display_text = (b['text'][:40] + '...') if len(b['text']) > 40 else b['text']
    #     print(f"║ {b['bubble_id']:<12} ║ {display_text:<43} ║")

    for b in bubbles:
        display_text = b['text']
        print(f"║ {b['bubble_id']:<12} ║ {display_text:<63} ║")

    print("╚" + "═" * 14 + "╩" + "═" * 44 + "╝")
    print(f"Łącznie wykryto dymków: {len(bubbles)}\n")


def draw_bubbles(image, bubbles, scale_fn, color=(255, 0, 255), thickness=4):
    """
    Rysuje ramki wokół całych dymków (bloków tekstu).
    :param image: Obraz oryginalny
    :param bubbles: Lista słowników z dymkami (wynik group_lines_into_bubbles)
    :param scale_fn: Funkcja to_original_coords
    :param color: Kolor BGR (domyślnie fioletowy)
    :param thickness: Grubość linii
    """
    for b in bubbles:
        orig_box = scale_fn(b["box_coords"])

        # ✅ wymagany kształt dla cv2.polylines: (N, 1, 2)
        pts = np.array(orig_box, dtype=np.int32).reshape((-1, 1, 2))

        cv2.polylines(image, [pts], isClosed=True, color=color, thickness=thickness)

        x1, y1 = pts[0][0]  # zmiana — bo teraz pts[0] to [[x, y]], nie [x, y]
        label = f"{b['bubble_id']} ({b['line_count']} lines)"
        cv2.putText(image, label, (int(x1), int(y1) - 15),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)

    return image


def make_marker_record(slice_id: int, y_start: int, marker_pos: tuple,
                       marker_item: dict | None, angle: int) -> dict:
    """
    Build a marker diagnostic record for draw_marker_debug.
    Call once per slice inside run_ocr_sliced.

    :param slice_id:    Current slice index.
    :param y_start:     Slice start Y in scaled coords.
    :param marker_pos:  (x, y) injected position in scaled slice-local coords.
    :param marker_item: OCR item for the detected marker, or None.
    :param angle:       Rotation angle that was applied (0 = none).
    :return: Dict ready to append to marker_records list.
    """
    marker_item_global = None
    if marker_item is not None:
        shifted_box = [[px, py + y_start] for px, py in marker_item["box"]]
        marker_item_global = {**marker_item, "box": shifted_box}

    return {
        "slice_id":    slice_id,
        "y_start":     y_start,
        "marker_pos":  marker_pos,
        "marker_item": marker_item_global,
        "angle":       angle,
    }


def draw_marker_debug(image: np.ndarray, marker_records: list, scale: float) -> np.ndarray:
    """
    Draw marker diagnostics on the final image.

    For each slice shows:
      GREEN cross+circle : injected (expected) marker position
      ORANGE polyline    : OCR-detected marker bounding box
      RED cross          : center of detected box
      YELLOW line        : expected → detected center
      CYAN label         : slice id, detected text, angle applied

    :param image:          Original-scale image (modified in-place).
    :param marker_records: List of dicts built by make_marker_record().
    :param scale:          SCALE value used during OCR.
    :return: Image with debug overlays.
    """
    h, w = image.shape[:2]

    COLOR_EXPECTED = (0,   200,   0)
    COLOR_DETECTED = (0,   140, 255)
    COLOR_CENTER   = (0,     0, 255)
    COLOR_LINE     = (0,   255, 255)
    COLOR_LABEL    = (255, 255,   0)
    CROSS_SIZE     = 12

    def _cross(img, cx, cy, size, color, thickness=2):
        cx, cy = max(0, min(int(cx), w - 1)), max(0, min(int(cy), h - 1))
        cv2.line(img, (cx - size, cy), (cx + size, cy), color, thickness)
        cv2.line(img, (cx, cy - size), (cx, cy + size), color, thickness)

    def _clamp(pts, iw, ih):
        pts[..., 0] = np.clip(pts[..., 0], 0, iw - 1)
        pts[..., 1] = np.clip(pts[..., 1], 0, ih - 1)
        return pts

    def _text(img, txt, x, y):
        x, y = max(0, min(int(x), w - 1)), max(1, min(int(y), h - 1))
        avail = w - x
        while txt:
            (tw, _), _ = cv2.getTextSize(txt, cv2.FONT_HERSHEY_SIMPLEX, 0.45, 1)
            if tw <= avail:
                break
            txt = txt[:-1]
        if txt:
            cv2.putText(img, txt, (x, y), cv2.FONT_HERSHEY_SIMPLEX, 0.45, COLOR_LABEL, 1)

    for rec in marker_records:
        mx_local, my_local = rec["marker_pos"]
        ex = int(mx_local / scale)
        ey = int((my_local + rec["y_start"]) / scale)
        ex, ey = max(0, min(ex, w - 1)), max(0, min(ey, h - 1))

        _cross(image, ex, ey, CROSS_SIZE, COLOR_EXPECTED, 2)
        cv2.circle(image, (ex, ey), CROSS_SIZE + 4, COLOR_EXPECTED, 1)

        label_parts = [f"S{rec['slice_id']}"]
        dx, dy = ex, ey

        if rec["marker_item"] is not None:
            pts = np.array(
                [[int(px / scale), int(py / scale)] for px, py in rec["marker_item"]["box"]],
                dtype=np.int32
            ).reshape((-1, 1, 2))
            pts = _clamp(pts, w, h)
            cv2.polylines(image, [pts], isClosed=True, color=COLOR_DETECTED, thickness=2)
            dx, dy = int(np.mean(pts[:, 0, 0])), int(np.mean(pts[:, 0, 1]))
            _cross(image, dx, dy, CROSS_SIZE - 4, COLOR_CENTER, 2)
            cv2.line(image, (ex, ey), (dx, dy), COLOR_LINE, 1)
            label_parts.append(rec["marker_item"].get("text", "?"))
        else:
            label_parts.append("NOT FOUND")

        label_parts.append(f"{rec['angle']}°")
        _text(image, "  ".join(label_parts), max(0, ex - 5), max(15, ey - CROSS_SIZE - 6))

    return image


def draw_marker_relocation_debug(image: np.ndarray, marker_records: list, scale: float) -> np.ndarray:
    """
    Rysuje gdzie były problemy z relokacją markerów.

    Zaznacza:
    - Czerwony "X" – marker w tekście (wstrzyknięty na default)
    - Zielony "+" – marker po relokacji
    - Żółta linia – połączenie
    """
    h, w = image.shape[:2]

    for rec in marker_records:
        if rec["marker_item"] is None:
            continue

        mx_orig, my_orig = rec["marker_pos"]

        # Wstrzyknięta pozycja na skalę oryginalną
        mx_scaled = int(mx_orig / scale)
        my_scaled = int((my_orig + rec["y_start"]) / scale)

        # Wykryta pozycja
        if rec["marker_item"]["box"]:
            pts = rec["marker_item"]["box"]
            # Dodane rzutowanie na float() chroni przed numpy overflow
            cx = int(sum(float(p[0]) for p in pts) / 4.0 / scale)
            cy = int(sum(float(p[1]) for p in pts) / 4.0 / scale)

            # Zielony "+" — gdzie faktycznie znaleziono
            cv2.drawMarker(image, (cx, cy), (0, 255, 0), cv2.MARKER_CROSS, 15, 2)
            cv2.putText(image, f"S{rec['slice_id']}", (cx + 5, cy - 5),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 255, 0), 1)

            # Żółta linia — wstrzyknięte vs znalezione
            cv2.line(image, (mx_scaled, my_scaled), (cx, cy), (0, 255, 255), 1)

    return image