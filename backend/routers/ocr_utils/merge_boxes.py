# merge_boxes.py

# --- FUNKCJE POMOCNICZE ---
def box_stats(box):
    # Wymuszamy float64 dla bardzo długich obrazów
    xs = [float(p[0]) for p in box]
    ys = [float(p[1]) for p in box]
    x1, x2 = min(xs), max(xs)
    y1, y2 = min(ys), max(ys)
    return {
        "x1": x1, "x2": x2, "y1": y1, "y2": y2,
        "cx": (x1 + x2) / 2.0,
        "cy": (y1 + y2) / 2.0,
        "w": max(1.0, x2 - x1),
        "h": max(1.0, y2 - y1),
    }


def vertical_overlap(a, b):
    inter = max(0.0, min(a["y2"], b["y2"]) - max(a["y1"], b["y1"]))
    return inter / max(1.0, min(a["h"], b["h"]))


def same_line(a, b, y_tol=None):
    """
    Sprawdza czy dwa elementy OCR leżą na tej samej linii tekstu.

    Używa wyłącznie odległości środków Y (cy) — bez vertical_overlap.

    Dlaczego usunięto vertical_overlap:
    PaddleOCR zwraca wysokie boxy (np. 60px) przy odstępie wierszy ~28px,
    co daje ~53% nakładania się sąsiednich wierszy. Przy overlap_min=0.35
    powodowało to łączenie kolejnych wierszy przez "chaining" — każdy
    nowy item był w y_tol od poprzedniego, wciągając cały pionowy dymek
    do jednej linii, a potem sortowanie po x1 dawało odwróconą kolejność.

    y_tol = 30% wysokości mniejszego boxa (było 40%):
    - dla h=60px: y_tol=18px  → rozdziela wiersze oddalone o ~22–28px ✓
    - dla h=30px: y_tol=9px   → bezpiecznie łączy itemy na tej samej linii ✓
    """
    if y_tol is None:
        y_tol = max(5.0, 0.3 * min(a["h"], b["h"]))
    return abs(a["cy"] - b["cy"]) <= y_tol


def join_gap(a, b):
    return max(0.0, b["x1"] - a["x2"])


def split_line_by_gap(line_items, gap_multiplier=3.0, image_width=None):
    """
    Rozcina linię na osobne fragmenty jeśli między elementami jest zbyt duża przerwa pozioma.

    Zapobiega łączeniu słów z różnych dymków, które leżą na tej samej wysokości Y.

    :param line_items:      Lista elementów OCR należących do jednej linii (posortowana po x1).
    :param gap_multiplier:  Ile razy szersza od średniego słowa może być przerwa. Domyślnie 3.0.
    :param image_width:     Szerokość obrazu w pikselach (skalowanego). Gdy podana, przerwa
                            nie może przekroczyć 10% szerokości obrazu — to uziemia próg
                            bezwzględnie i zapobiega łączeniu sąsiednich dymków, których
                            tekst leży na tej samej wysokości Y z przerwą mniejszą niż
                            avg_w * gap_multiplier, ale i tak widocznie za dużą jak na
                            pojedynczy dymek (np. 140 px przy avg_w=100 px i mult=3.0 → 300 px).
    :return: Lista list — każda podlista to osobny fragment linii (osobny dymek).

    Przykład problemu bez image_width:
        "THEN HOW" @ x=130,  "TO STOP" @ x=390  → przerwa ~140 px
        avg_w≈100 px,  max_gap = 3.0 × 100 = 300 px  → NIE rozdzielone ✗

    Z image_width=1252:
        abs_cap = 1252 × 0.10 = 125 px
        max_gap = min(300, 125) = 125 px  → przerwa 140 > 125 → ROZDZIELONE ✓
    """
    if len(line_items) <= 1:
        return [line_items]

    sorted_items = sorted(line_items, key=lambda x: x["stats"]["x1"])
    avg_w = sum(i["stats"]["w"] for i in sorted_items) / len(sorted_items)
    max_gap = avg_w * gap_multiplier

    # Bezwzględny cap: słowa wewnątrz dymka są oddalone o ~10–40 px;
    # przerwa >10% szerokości obrazu prawie na pewno oznacza inny dymek.
    if image_width and image_width > 0:
        abs_cap = image_width * 0.05
        max_gap = min(max_gap, abs_cap)

    chunks = [[sorted_items[0]]]
    for item in sorted_items[1:]:
        gap = item["stats"]["x1"] - chunks[-1][-1]["stats"]["x2"]
        if gap > max_gap:
            chunks.append([item])
        else:
            chunks[-1].append(item)

    return chunks


# --- FUNKCJA GRUPOWANIA ---
def build_text_lines(items, image_width=None):
    """
    Grupuje pojedyncze elementy OCR w linie, nie modyfikując oryginalnych danych.
    Zwraca listę słowników reprezentujących całe linie tekstu.

    Po zgrubionym grupowaniu po Y każda linia jest dodatkowo rozbijana przez
    split_line_by_gap — zapobiega to łączeniu słów z różnych dymków leżących
    na tej samej wysokości.

    :param image_width: Szerokość obrazu skalowanego w px. Przekazywana do
                        split_line_by_gap — patrz opis tamtej funkcji.
    """
    if not items:
        return []

    # 1. Upewniamy się, że każdy oryginalny element ma unikalne ID
    for idx, item in enumerate(items):
        if "id" not in item:
            item["id"] = f"word_{idx}"

    # 2. Obliczamy statystyki do sortowania (na kopii danych, by nie śmiecić w items)
    enriched = []
    for item in items:
        stats = box_stats(item["box"])
        enriched.append({**item, "stats": stats})

    # 3. Zgrubne sortowanie od góry do dołu
    enriched.sort(key=lambda x: x["stats"]["cy"])

    raw_lines = []
    for item in enriched:
        added = False
        for line in raw_lines:
            if same_line(item["stats"], line[-1]["stats"]):
                line.append(item)
                added = True
                break
        if not added:
            raw_lines.append([item])

    # 4. Rozbijamy każdą linię przez split_line_by_gap — fix dla słów z różnych dymków
    #    które leżą na tej samej wysokości Y
    lines = []
    for raw_line in raw_lines:
        raw_line.sort(key=lambda x: x["stats"]["x1"])
        chunks = split_line_by_gap(raw_line, image_width=image_width)
        lines.extend(chunks)

    grouped_lines = []

    # 5. Sortowanie w poziomie, budowanie oddzielnej struktury dla linii
    for line_idx, line in enumerate(lines):
        line.sort(key=lambda x: x["stats"]["x1"])

        merged_text = " ".join([i["text"] for i in line])
        word_ids = [i["id"] for i in line]
        avg_score = sum(i["score"] for i in line) / len(line)

        min_x = min(i["stats"]["x1"] for i in line)
        min_y = min(i["stats"]["y1"] for i in line)
        max_x = max(i["stats"]["x2"] for i in line)
        max_y = max(i["stats"]["y2"] for i in line)

        merged_box = [
            [min_x, min_y], [max_x, min_y],
            [max_x, max_y], [min_x, max_y]
        ]

        grouped_lines.append({
            "line_id": f"line_{line_idx}",
            "word_ids": word_ids,
            "text": merged_text,
            "score": avg_score,
            "box": merged_box
        })

    return grouped_lines


def group_lines_into_bubbles(grouped_lines, x_overlap_thresh=0.5, y_dist_mult=1.5, min_score=0.7):
    """
    Ulepszone grupowanie z filtrem pewności i limitem fizycznego dystansu.

    Dodatkowo sprawdza odległość środków linii w poziomie, co zapobiega łączeniu
    dymków stojących obok siebie (ten sam Y, różne X) gdy ich boxy w osi X
    przypadkowo nachodzą na siebie.
    """
    if not grouped_lines:
        return []

    # 1. Filtrujemy totalne śmieci na wejściu (opcjonalnie)
    lines = [l for l in grouped_lines if l["score"] >= min_score]
    if not lines:
        return []

    lines = sorted(lines, key=lambda l: box_stats(l["box"])["cy"])
    bubbles_list = []

    for line in lines:
        l_stats = box_stats(line["box"])
        added = False

        for b_group in bubbles_list:
            last_line = b_group[-1]
            b_stats = box_stats(last_line["box"])

            # Oblicz dystans w pionie
            vertical_gap = l_stats["y1"] - b_stats["y2"]

            # Jeśli przerwa jest większa niż abs_gap_limit, to na 99% inny dymek
            abs_gap_limit = 120.0
            max_gap = min(abs_gap_limit, b_stats["h"] * y_dist_mult)

            # Pokrycie w poziomie (krawędziowe)
            overlap_x = max(0, min(l_stats["x2"], b_stats["x2"]) - max(l_stats["x1"], b_stats["x1"]))
            min_w = min(l_stats["w"], b_stats["w"])
            x_overlap_ratio = overlap_x / min_w

            # Dodatkowe sprawdzenie: środki linii nie mogą być zbyt daleko od siebie w osi X.
            # Zapobiega łączeniu dymków kolumnowych / dwóch kolumn w jednym panelu.
            cx_dist = abs(l_stats["cx"] - b_stats["cx"])
            max_cx_dist = (l_stats["w"] + b_stats["w"]) * 0.45

            if (vertical_gap < max_gap
                    and x_overlap_ratio > x_overlap_thresh
                    and cx_dist < max_cx_dist):
                b_group.append(line)
                added = True
                break

        if not added:
            bubbles_list.append([line])

    # 2. Budowanie wynikowych dymków
    merged_bubbles = []
    for idx, b_group in enumerate(bubbles_list):
        # b_group jest już posortowany po cy (pętla wyżej iteruje linie top-to-bottom)
        # więc tekst jest w prawidłowej kolejności od góry do dołu
        combined_text = " ".join([l["text"] for l in b_group])

        all_points = []
        for l in b_group:
            all_points.extend(l["box"])

        stats = box_stats(all_points)
        merged_box = [
            [stats["x1"], stats["y1"]], [stats["x2"], stats["y1"]],
            [stats["x2"], stats["y2"]], [stats["x1"], stats["y2"]]
        ]

        merged_bubbles.append({
            "bubble_id": f"bubble_{idx}",
            "text": combined_text,
            "box_coords": merged_box,
            "line_count": len(b_group),
            "avg_score": sum(l["score"] for l in b_group) / len(b_group)
        })

    return merged_bubbles