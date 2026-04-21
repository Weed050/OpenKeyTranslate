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
        "cy": (y1 + y2) / 2.0, # Tutaj moze byc overflow przy duzym y w obrazkach
        "w": max(1.0, x2 - x1),
        "h": max(1.0, y2 - y1),
    }


def vertical_overlap(a, b):
    inter = max(0.0, min(a["y2"], b["y2"]) - max(a["y1"], b["y1"]))
    return inter / max(1.0, min(a["h"], b["h"]))


def same_line(a, b, y_tol=None, overlap_min=0.35):
    if y_tol is None:
        y_tol = max(6.0, 0.4 * min(a["h"], b["h"]))
    return (abs(a["cy"] - b["cy"]) <= y_tol) or (vertical_overlap(a, b) >= overlap_min)


def join_gap(a, b):
    return max(0.0, b["x1"] - a["x2"])


# --- NOWA FUNKCJA GRUPOWANIA ---
def build_text_lines(items):
    """
    Grupuje pojedyncze elementy OCR w linie, nie modyfikując oryginalnych danych.
    Zwraca listę słowników reprezentujących całe linie tekstu.
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

    lines = []
    for item in enriched:
        added = False
        for line in lines:
            if same_line(item["stats"], line[-1]["stats"]):
                line.append(item)
                added = True
                break
        if not added:
            lines.append([item])

    grouped_lines = []

    # 4. Sortowanie w poziomie, budowanie oddzielnej struktury dla linii
    for line_idx, line in enumerate(lines):
        line.sort(key=lambda x: x["stats"]["x1"])

        merged_text = " ".join([i["text"] for i in line])
        word_ids = [i["id"] for i in line]  # Zapisujemy ID słów składowych
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
            "word_ids": word_ids,  # lista id tekstow
            "text": merged_text,
            "score": avg_score,
            "box": merged_box
        })

    return grouped_lines


def group_lines_into_bubbles(grouped_lines, x_overlap_thresh=0.5, y_dist_mult=1.5, min_score=0.7):
    """
    Ulepszone grupowanie z filtrem pewności i limitem fizycznego dystansu.
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

            # --- ZABEZPIECZENIE ---
            # Jeśli przerwa jest większa niż np. 120 px, to na 99% nie jest ten sam dymek
            # niezależnie od tego, co mówi mnożnik wysokości.
            abs_gap_limit = 120.0
            max_gap = min(abs_gap_limit, b_stats["h"] * y_dist_mult)

            # Pokrycie w poziomie
            overlap_x = max(0, min(l_stats["x2"], b_stats["x2"]) - max(l_stats["x1"], b_stats["x1"]))
            min_w = min(l_stats["w"], b_stats["w"])
            x_overlap_ratio = overlap_x / min_w

            # Łączymy tylko jeśli:
            # 1. Przerwa w pionie jest mała
            # 2. Linie nachodzą na siebie w poziomie
            # 3. Dodatkowo: Środki linii nie są od siebie zbyt oddalone (opcjonalnie)
            if vertical_gap < max_gap and x_overlap_ratio > x_overlap_thresh:
                b_group.append(line)
                added = True
                break

        if not added:
            bubbles_list.append([line])

    # 2. Budowanie wynikowych dymków
    merged_bubbles = []
    for idx, b_group in enumerate(bubbles_list):
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