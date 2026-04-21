import cv2
import numpy as np

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

    for b in bubbles:
        # Skracanie tekstu do konsoli, jeśli jest za długi
        display_text = (b['text'][:40] + '...') if len(b['text']) > 40 else b['text']
        print(f"║ {b['bubble_id']:<12} ║ {display_text:<43} ║")

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
        # Skalujemy box dymka
        orig_box = scale_fn(b["box_coords"])  # używamy koordynatów z box_stats dymka

        pts = np.array(orig_box, dtype=np.int32)

        # Rysujemy ramkę dymka
        cv2.polylines(image, [pts], isClosed=True, color=color, thickness=thickness)

        # Podpisujemy ID dymka i liczbę linii
        x1, y1 = pts[0]
        label = f"{b['bubble_id']} ({b['line_count']} lines)"
        cv2.putText(image, label, (int(x1), int(y1) - 15),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)

    return image
