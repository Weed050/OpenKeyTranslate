import numpy as np
import re
from rapidfuzz import fuzz

# handle_duplicates.py

def normalize(word: str) -> str:
    """Remove punctuation, keep only alphanumeric characters."""
    return re.sub(r'\W', '', word.lower())


def shared_words_ratio(text1, text2):
    """Fraction of words shared between two texts, relative to the smaller set."""
    words1 = set(normalize(w) for w in text1.split())
    words2 = set(normalize(w) for w in text2.split())
    if not words1 or not words2:
        return 0.0
    common = words1 & words2
    return len(common) / min(len(words1), len(words2))


def find_suffix_prefix_overlap(text1: str, text2: str) -> list[str] | None:
    """
    Check whether the end of text1 overlaps with the beginning of text2.

    Finds the longest possible suffix/prefix overlap.
    Example: "Hello World" + "World Bye" → ["world"]
             "Hello"       + "World Bye" → None

    :param text1: Text of the first item (higher score, kept intact)
    :param text2: Text of the second item (lower score, candidate for trimming)
    :return: List of overlapping words, or None if no overlap found
    """
    words1 = text1.lower().split()
    words2 = text2.lower().split()

    best = None
    for n in range(1, min(len(words1), len(words2)) + 1):
        if [normalize(w) for w in words1[-n:]] == [normalize(w) for w in words2[:n]]:
            best = words2[:n]

    return best


def boxes_overlap(box1, box2, min_area=5):
    """
    Returns (inter_area, overlap_ratio_of_smaller_box).
    min_area: ignore overlaps smaller than N pixels (1px noise guard)
    """
    def to_rect(box):
        pts = np.array(box)
        return pts[:,0].min(), pts[:,1].min(), pts[:,0].max(), pts[:,1].max()

    x1a, y1a, x2a, y2a = to_rect(box1)
    x1b, y1b, x2b, y2b = to_rect(box2)

    xi1, yi1 = max(x1a, x1b), max(y1a, y1b)
    xi2, yi2 = min(x2a, x2b), min(y2a, y2b)

    inter = int(max(0, xi2 - xi1)) * int(max(0, yi2 - yi1))

    if inter < min_area:
        return 0, 0.0

    # cast to float to avoid int32 overflow on large images (y coords ~21000+)
    area_a = float(x2a - x1a) * float(y2a - y1a)
    area_b = float(x2b - x1b) * float(y2b - y1b)
    smaller = min(area_a, area_b)

    return inter, (inter / smaller if smaller > 0 else 0.0)


def estimate_trim_x(box, overlap_words, all_words, side) -> tuple[float, float, float, float]:
    """
    Geometrically estimate new X coordinates after trimming a box.

    Assumes uniform character distribution across the box width — the ratio
    of characters in the overlapping text to total characters maps to the
    fraction of box width to cut off.

    Example:
        box x: 80 → 280  (width 200px)
        all_words: ["world", "bye"]   → "world bye" (9 chars)
        overlap_words: ["world"]      → "world"     (5 chars)
        ratio = 5/9 = 0.55
        side='left' → new x_min = 80 + 200*0.55 = 190

    :param box:           Four polygon points [[x,y],[x,y],[x,y],[x,y]]
    :param overlap_words: Words to cut off (the overlapping fragment)
    :param all_words:     All words in this box
    :param side:          'left'  → trim from left  (remove prefix)
                          'right' → trim from right (remove suffix)
    :return: Tuple (x_min_new, y_min, x_max_new, y_max) — new box coordinates
    """
    pts   = np.array(box)
    x_min = float(pts[:, 0].min())
    x_max = float(pts[:, 0].max())
    y_min = float(pts[:, 1].min())
    y_max = float(pts[:, 1].max())
    width = x_max - x_min

    # use character count instead of word count — better reflects physical text width
    overlap_chars = sum(len(w) for w in overlap_words)
    all_chars     = max(sum(len(w) for w in all_words), 1)
    ratio = overlap_chars / all_chars

    if side == 'left':
        # remove prefix from left — x_min shifts right
        x_min_new = x_min + width * ratio
        x_max_new = x_max
    else:
        # remove suffix from right — x_max shifts left
        x_min_new = x_min
        x_max_new = x_max - width * ratio

    return x_min_new, y_min, x_max_new, y_max


def trim_and_re_ocr(
    image: np.ndarray,
    box: list[list[float]],
    x_min_new: float,
    y_min: float,
    x_max_new: float,
    y_max: float,
    ocr
) -> tuple[str | None, list[list[int]] | None, list[list[int]] | None]:
    """
    Crop a trimmed region from the image, run OCR on it, and return the results.

    Returns two boxes:
    - box_crop:     coordinates of the cropped region in image space
                    (for debug drawing — shows where we searched)
    - box_adjusted: coordinates fitted to the re-OCR result
                    (may be narrower than box_crop if OCR found a smaller area)

    :param image:     Source image (scaled) as numpy array (H, W, C)
    :param box:       Original polygon before trimming [[x,y], ...]
    :param x_min_new: New left X boundary after trimming
    :param y_min:     Top Y boundary (unchanged)
    :param x_max_new: New right X boundary after trimming
    :param y_max:     Bottom Y boundary (unchanged)
    :param ocr:       PaddleOCR instance
    :return: Tuple (new_text, box_crop, box_adjusted)
             - new_text:      Recognized text after re-OCR, or None if no result
             - box_crop:      Cropped region box in image coordinates
             - box_adjusted:  Re-OCR result box in image coordinates
             All values None if re-OCR failed or crop is empty
    """
    xi1 = int(x_min_new)
    yi1 = int(y_min)
    xi2 = int(x_max_new)
    yi2 = int(y_max)

    crop = image[yi1:yi2, xi1:xi2]

    if crop.size == 0:
        return None, None, None

    # box_crop — physical region we cut out (for debug drawing)
    box_crop = [
        [xi1, yi1],
        [xi2, yi1],
        [xi2, yi2],
        [xi1, yi2],
    ]

    result = ocr.predict(crop)

    if not result:
        return None, None, None

    new_text       = None
    best_score     = 0.0
    best_local_box = None  # box in local crop coordinates

    for res in result:
        for t, s, b in zip(res["rec_texts"], res["rec_scores"], res["dt_polys"]):
            if s > best_score:
                best_score     = s
                new_text       = t
                best_local_box = b

    if new_text is None:
        return None, None, None

    # shift local OCR box back to global image coordinates
    if best_local_box is not None:
        box_adjusted = [[int(x + xi1), int(y + yi1)] for x, y in best_local_box]
    else:
        box_adjusted = box_crop  # fallback — use the full crop box

    return new_text, box_crop, box_adjusted


def resolve_overlap_pair(
    item_a: dict,
    item_b: dict,
    image: np.ndarray,
    ocr
) -> dict | None:
    """
    Try to resolve a duplicate pair by trimming the lower-scored item.

    item_a has a higher score — kept intact.
    item_b has a lower score — candidate for trimming.

    Two suffix/prefix cases are checked:
    CASE 1: end of A = start of B  →  trim left side of B
        "Hello World" + "World Bye"  →  B becomes "Bye"
    CASE 2: end of B = start of A  →  trim right side of B
        "World Bye" (A) + "Hello World" (B)  →  B becomes "Hello"

    :param item_a:  Higher-score item, dict with keys: text, score, box, slice_id
    :param item_b:  Lower-score item, candidate for trimming
    :param image:   Source image (scaled) as numpy array
    :param ocr:     PaddleOCR instance
    :return: Corrected item_b as a new dict, or None if repair failed
    """
    words_b = item_b["text"].split()

    # --- CASE 1: suffix of A = prefix of B → trim left side of B ---
    overlap = find_suffix_prefix_overlap(item_a["text"], item_b["text"])

    if overlap:
        print(f"[DEDUP] case 1 — overlap: {overlap}")
        x_min_new, y_min, x_max_new, y_max = estimate_trim_x(
            item_b["box"], overlap, words_b, side='left'
        )
        new_text, box_crop, box_adjusted = trim_and_re_ocr(
            image, item_b["box"], x_min_new, y_min, x_max_new, y_max, ocr
        )

        if new_text:
            new_words = set(normalize(w) for w in new_text.split())
            if not any(normalize(w) in new_words for w in overlap):
                print(f"[DEDUP][OK] '{item_b['text']}' → '{new_text}'")
                print(f"[DEDUP]    box_crop={box_crop}")
                print(f"[DEDUP]    box_adjusted={box_adjusted}")
                return {**item_b, "text": new_text, "box": box_adjusted}
        else:
            print(f"[DEDUP][!] case 1 failed, result: '{new_text}'")

    # --- CASE 2: suffix of B = prefix of A → trim right side of B ---
    overlap_rev = find_suffix_prefix_overlap(item_b["text"], item_a["text"])

    if overlap_rev:
        print(f"[DEDUP] case 2 — overlap_rev: {overlap_rev}")
        x_min_new, y_min, x_max_new, y_max = estimate_trim_x(
            item_b["box"], overlap_rev, words_b, side='right'
        )
        new_text, box_crop, box_adjusted = trim_and_re_ocr(
            image, item_b["box"], x_min_new, y_min, x_max_new, y_max, ocr
        )

        if new_text:
            new_words = set(normalize(w) for w in new_text.split())

            if not any(normalize(w) in new_words for w in overlap_rev):
                print(f"[DEDUP][OK] '{item_b['text']}' → '{new_text}'")
                print(f"[DEDUP]    box_crop={box_crop}")
                print(f"[DEDUP]    box_adjusted={box_adjusted}")
                return {**item_b, "text": new_text, "box": box_adjusted}
            else:
                print(f"[DEDUP][!] case 2 failed, result: '{new_text}'")

    return None  # neither case succeeded


def smart_deduplicate_by_lines(
    grouped_lines: list[dict],
    items: list[dict],
    image: np.ndarray,
    ocr,
    word_thresh: float = 0.4,
    geo_thresh: float  = 0.2,
    min_overlap_area: int = 50
) -> list[dict]:
    """
    Line-aware deduplication — compares only items within the same text line.

    Slice-boundary duplicates always appear on the same horizontal line, so
    cross-line comparisons are unnecessary and risk false positives.
    Accepts pre-grouped lines to avoid duplicating grouping logic here.

    :param grouped_lines:    Output of build_text_lines(items)
    :param items:            Flat list of OCR items (text, score, box, slice_id, id)
    :param image:            Scaled source image as numpy array (H, W, C)
    :param ocr:              PaddleOCR instance
    :param word_thresh:      Min shared-word fraction to check geometry (0.4 = 40%)
    :param geo_thresh:       Min overlap fraction of smaller box to flag duplicate (0.2 = 20%)
    :param min_overlap_area: Min overlap area in px² below which overlap is ignored (noise guard)
    :return: Deduplicated list of items with corrected boxes and texts
    """
    result = []

    for line in grouped_lines:
        line_word_ids = set(line["word_ids"])
        line_items = [item for item in items if item["id"] in line_word_ids]

        # sort by score descending — higher-score item stays intact
        line_items = sorted(line_items, key=lambda x: x["score"], reverse=True)
        kept = []

        for candidate in line_items:
            conflict_idx = None

            for i, kept_item in enumerate(kept):
                word_ratio = shared_words_ratio(candidate["text"], kept_item["text"])
                if word_ratio < word_thresh:
                    continue  # too few shared words — skip geometry check

                _, overlap_ratio = boxes_overlap(
                    candidate["box"], kept_item["box"], min_overlap_area
                )
                if overlap_ratio > geo_thresh:
                    conflict_idx = i
                    break  # conflict found — exit inner loop

            if conflict_idx is None:
                kept.append(candidate)
                continue

            # conflict found → try to repair by trimming + re-OCR
            fixed = resolve_overlap_pair(kept[conflict_idx], candidate, image, ocr)

            if fixed:
                kept.append(fixed)
            else:
                y_center = int(sum(float(p[1]) for p in candidate["box"]) / 4.0)
                print(f"[DEDUP] fallback NMS drop at Y={y_center}: '{candidate['text']}'")

        result.extend(kept)

    return result


def remove_slice_boundary_duplicates(items: list[dict], max_y_jitter: int = 160) -> list[dict]:
    """
    Usuwa duplikaty graniczne używając POTRÓJNEJ walidacji:
    1. Geometria (boxy muszą na siebie nachodzić)
    2. Tekst (Fuzzy match musi być > 60%, żeby wyłapać literówki OCR typu BUG'S / BUO'5)
    3. Score (wybiera wariant z najwyższą pewnością modelu)
    """
    to_remove = set()

    for i in range(len(items)):
        if i in to_remove:
            continue

        item_i = items[i]
        slice_i = item_i.get("slice_id", -1)
        text_i = normalize(item_i["text"])

        for j in range(i + 1, len(items)):
            if j in to_remove:
                continue

            item_j = items[j]
            slice_j = item_j.get("slice_id", -1)

            # 1. Ignoruj duplikaty z tego samego slice'a (zrobi to smart_deduplicate na końcu)
            if slice_i == slice_j:
                continue

            # 2. KRYTERIUM: GEOMETRIA (Położenie)
            _, overlap = boxes_overlap(item_i["box"], item_j["box"], min_area=0)
            if overlap > 0.3:

                # 3. KRYTERIUM: TEKST (Podobieństwo)
                text_j = normalize(item_j["text"])
                similarity = fuzz.ratio(text_i, text_j) / 100.0

                # Jeśli są podobne w co najmniej 60%
                if similarity >= 0.60:

                    # 4. KRYTERIUM: SCORE (Kto wygrywa)
                    if item_i["score"] >= item_j["score"]:
                        print(f"[BOUNDARY DEDUP] drop S{slice_j}: '{item_j['text']}' (score: {item_j['score']:.2f}) "
                              f"-> duplikat S{slice_i}: '{item_i['text']}' (score: {item_i['score']:.2f}) (sim: {similarity:.2f})")
                        to_remove.add(j)
                    else:
                        print(f"[BOUNDARY DEDUP] drop S{slice_i}: '{item_i['text']}' (score: {item_i['score']:.2f}) "
                              f"-> duplikat S{slice_j}: '{item_j['text']}' (score: {item_j['score']:.2f}) (sim: {similarity:.2f})")
                        to_remove.add(i)
                        break  # item_i usunięty, przechodzimy do kolejnego 'i'

    final_items = [item for idx, item in enumerate(items) if idx not in to_remove]
    print(f"[BOUNDARY DEDUP] Usunięto {len(to_remove)} twardych duplikatów z granicy sliców (Geo + Text + Score).")
    return final_items