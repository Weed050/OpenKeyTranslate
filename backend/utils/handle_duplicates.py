
# handle_duplicates.py

"""
Provides utilities for deduplicating and repairing overlapping OCR text boxes.

Since large images are cut into overlapping slices to prevent text loss,
redundant text boxes frequently occur. This module resolves these conflicts
by comparing text content, geometric overlap, and OCR confidence scores.

It handles two main scenarios:
1. Slice Boundary Deduplication: Identifies duplicated text boxes across
   different image slices. Because text near a slice edge is often cut off
   and misread by the OCR, the module uses fuzzy matching to catch non-1:1
   duplicates. It then safely discards the shorter, lower-confidence text.
2. Same-Line Text Repair: When text boxes partially overlap within the same
   line, the module identifies the less accurate text, dynamically trims the
   overlapping section from the image, and re-runs the OCR to repair and
   extract the correct text.
"""

import numpy as np
import re
from rapidfuzz import fuzz

def normalize(word: str) -> str:
    """
    Remove punctuation and keep only alphanumeric characters for clean
    string comparisons.
    """
    return re.sub(r'\W', '', word.lower())


def shared_words_ratio(text1, text2):
    """
    Calculate the fraction of words shared between two texts.
    The ratio is calculated relative to the smaller set of words
    to ensure fairness.
    """
    words1 = set(normalize(w) for w in text1.split())
    words2 = set(normalize(w) for w in text2.split())
    if not words1 or not words2:
        return 0.0
    common = words1 & words2
    return len(common) / min(len(words1), len(words2))


def find_suffix_prefix_overlap(text1: str, text2: str) -> list[str] | None:
    """
    Determine if the end of the first text overlaps with the beginning of the second text.

    It searches for the longest possible overlapping sequence of words.

    Example:
        "Hello World" + "World Bye" -> ["world"]
        "Hello"       + "World Bye" -> None

    :param text1: Text of the first item (typically higher score, kept intact).
    :param text2: Text of the second item (typically lower score, candidate for trimming).
    :return: A list of overlapping words, or None if no overlap is found.
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
    Calculate the intersection and overlap ratio between two bounding boxes.

    :param min_area: Threshold to ignore tiny overlaps (acts as a 1px noise guard).
    :return: A tuple containing (intersection_area, overlap_ratio_relative_to_smaller_box).
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
    Geometrically estimate the new X coordinates after trimming a bounding box.

    This function assumes a uniform character distribution across the box width.
    The ratio of characters in the overlapping text compared to the total characters
    dictates the fraction of the box width that should be cut off.

    Example:
        box x: 80 -> 280 (width 200px)
        all_words: ["world", "bye"] -> "world bye" (9 chars)
        overlap_words: ["world"]    -> "world"     (5 chars)
        ratio = 5/9 = 0.55
        side = 'left' -> new x_min = 80 + (200 * 0.55) = 190

    :param box: The four polygon points representing the box [[x,y], [x,y], [x,y], [x,y]].
    :param overlap_words: The specific words intended to be cut off (the overlap).
    :param all_words: All words currently contained within this box.
    :param side: 'left' to trim from the left (remove prefix), 'right' to trim from the right (remove suffix).
    :return: A tuple of (x_min_new, y_min, x_max_new, y_max) representing the updated box coordinates.
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
    Crop the trimmed region from the image, perform OCR on it, and return the updated results.

    Provides two boxes:
    - box_crop: The coordinates of the cropped region in image space (useful for debug drawing).
    - box_adjusted: The coordinates fitted to the re-OCR result (may be narrower if OCR found a smaller area).

    :param image: Source image (scaled) as a numpy array.
    :param box: Original polygon before trimming [[x,y], ...].
    :param x_min_new: New left X boundary after trimming.
    :param y_min: Top Y boundary (remains unchanged).
    :param x_max_new: New right X boundary after trimming.
    :param y_max: Bottom Y boundary (remains unchanged).
    :param ocr: The PaddleOCR instance.
    :return: Tuple of (new_text, box_crop, box_adjusted). Returns None for all if re-OCR fails or crop is empty.
    """
    xi1 = int(x_min_new)
    yi1 = int(y_min)
    xi2 = int(x_max_new)
    yi2 = int(y_max)

    crop = image[yi1:yi2, xi1:xi2]

    if crop.size == 0:
        return None, None, None

    # Physical region we cut out (used for debug drawing)
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
    best_local_box = None  # Bounding box in local crop coordinates

    for res in result:
        for t, s, b in zip(res["rec_texts"], res["rec_scores"], res["dt_polys"]):
            if s > best_score:
                best_score     = s
                new_text       = t
                best_local_box = b

    if new_text is None:
        return None, None, None

    # Shift local OCR bounding box back to global image coordinates
    if best_local_box is not None:
        box_adjusted = [[int(x + xi1), int(y + yi1)] for x, y in best_local_box]
    else:
        # Fallback: use the full crop bounding box
        box_adjusted = box_crop

    return new_text, box_crop, box_adjusted


def resolve_overlap_pair(
    item_a: dict,
    item_b: dict,
    image: np.ndarray,
    ocr
) -> dict | None:
    """
    Attempt to resolve a duplicate pair by trimming the item with the lower score.

    item_a has a higher score and is kept intact.
    item_b has a lower score and is the candidate for trimming.

    Evaluates two cases:
    CASE 1: End of A overlaps with start of B -> trim the left side of B.
        Example: "Hello World" (A) + "World Bye" (B) -> B becomes "Bye"
    CASE 2: End of B overlaps with start of A -> trim the right side of B.
        Example: "World Bye" (A) + "Hello World" (B) -> B becomes "Hello"

    :param item_a: Higher-score item, dict containing text, score, box, and slice_id.
    :param item_b: Lower-score item, candidate for trimming.
    :param image: Source image (scaled) as a numpy array.
    :param ocr: The PaddleOCR instance.
    :return: The corrected item_b as a new dict, or None if the repair attempt failed.
    """
    words_b = item_b["text"].split()

    # --- CASE 1: suffix of A == prefix of B -> trim left side of B ---
    overlap = find_suffix_prefix_overlap(item_a["text"], item_b["text"])

    if overlap:
        print(f"[DEDUP] Case 1 - overlap: {overlap}")
        x_min_new, y_min, x_max_new, y_max = estimate_trim_x(
            item_b["box"], overlap, words_b, side='left'
        )
        new_text, box_crop, box_adjusted = trim_and_re_ocr(
            image, item_b["box"], x_min_new, y_min, x_max_new, y_max, ocr
        )

        if new_text:
            new_words = set(normalize(w) for w in new_text.split())
            if not any(normalize(w) in new_words for w in overlap):
                print(f"[DEDUP] [SUCCESS] '{item_b['text']}' -> '{new_text}'")
                print(f"[DEDUP]    box_crop={box_crop}")
                print(f"[DEDUP]    box_adjusted={box_adjusted}")
                return {**item_b, "text": new_text, "box": box_adjusted}
        else:
            print(f"[DEDUP] [WARNING] Case 1 failed, resulting text: '{new_text}'")

    # --- CASE 2: suffix of B = prefix of A → trim right side of B ---
    overlap_rev = find_suffix_prefix_overlap(item_b["text"], item_a["text"])

    if overlap_rev:
        print(f"[DEDUP] Case 2 - overlap_rev: {overlap_rev}")
        x_min_new, y_min, x_max_new, y_max = estimate_trim_x(
            item_b["box"], overlap_rev, words_b, side='right'
        )
        new_text, box_crop, box_adjusted = trim_and_re_ocr(
            image, item_b["box"], x_min_new, y_min, x_max_new, y_max, ocr
        )

        if new_text:
            new_words = set(normalize(w) for w in new_text.split())

            if not any(normalize(w) in new_words for w in overlap_rev):
                print(f"[DEDUP] [SUCCESS] '{item_b['text']}' -> '{new_text}'")
                print(f"[DEDUP]    box_crop={box_crop}")
                print(f"[DEDUP]    box_adjusted={box_adjusted}")
                return {**item_b, "text": new_text, "box": box_adjusted}
            else:
                print(f"[DEDUP] [WARNING] Case 2 failed, resulting text: '{new_text}'")

    return None  # Neither case succeeded


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
    Perform line-aware deduplication by comparing only items within the same text line.

    Since slice-boundary duplicates invariably appear on the same horizontal line,
    cross-line comparisons are unnecessary and carry a risk of false positives.
    This function accepts pre-grouped lines to avoid redundant grouping logic.

    :param grouped_lines: The output generated by build_text_lines(items).
    :param items: A flat list of OCR items (containing text, score, box, slice_id, id).
    :param image: The scaled source image as a numpy array (H, W, C).
    :param ocr: The PaddleOCR instance.
    :param word_thresh: Minimum shared-word fraction required to trigger a geometry check (0.4 = 40%).
    :param geo_thresh: Minimum overlap fraction of the smaller box to flag a duplicate (0.2 = 20%).
    :param min_overlap_area: Overlaps below this area (in px²) are ignored as noise.
    :return: A deduplicated list of items with corrected bounding boxes and texts.
    """
    result = []

    for line in grouped_lines:
        line_word_ids = set(line["word_ids"])
        line_items = [item for item in items if item["id"] in line_word_ids]

        # Sort by score descending: higher-score items are evaluated first and kept intact
        line_items = sorted(line_items, key=lambda x: x["score"], reverse=True)
        kept = []

        for candidate in line_items:
            conflict_idx = None

            for i, kept_item in enumerate(kept):
                word_ratio = shared_words_ratio(candidate["text"], kept_item["text"])
                if word_ratio < word_thresh:
                    continue  # Too few shared words; skip the geometry check

                _, overlap_ratio = boxes_overlap(
                    candidate["box"], kept_item["box"], min_overlap_area
                )
                if overlap_ratio > geo_thresh:
                    conflict_idx = i
                    break  # Conflict detected; break out of the inner loop

            if conflict_idx is None:
                kept.append(candidate)
                continue

            # Conflict detected: attempt to repair by trimming and re-evaluating OCR
            fixed = resolve_overlap_pair(kept[conflict_idx], candidate, image, ocr)

            if fixed:
                kept.append(fixed)
            else:
                y_center = int(sum(float(p[1]) for p in candidate["box"]) / 4.0)
                print(f"[DEDUP] [FALLBACK NMS DROP] Dropped item at Y={y_center}: '{candidate['text']}'")

        result.extend(kept)

    return result


def remove_slice_boundary_duplicates(items: list[dict], max_y_jitter: int = 160) -> list[dict]:
    """
    Remove boundary duplicates utilizing a TRIPLE validation mechanism:

    1. Geometry: Bounding boxes must overlap significantly.
    2. Text: Fuzzy match similarity must exceed 60% to catch OCR typos (e.g., BUG'S vs BUO'5).
    3. Score: Retains the variant with the highest model confidence.
    """
    to_remove = set()
    print('\n')

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

            # 1. CONSTRAINT: SLICE ORIGIN (Ignore duplicates from the same slice; smart_deduplicate handles these)
            if slice_i == slice_j:
                continue

            # 2. CONSTRAINT: GEOMETRY (Position Overlap)
            _, overlap = boxes_overlap(item_i["box"], item_j["box"], min_area=0)
            if overlap > 0.3:

                # 3. CONSTRAINT: TEXT SIMILARITY
                text_j = normalize(item_j["text"])
                similarity = fuzz.ratio(text_i, text_j) / 100.0

                # Check if texts are at least 60% similar
                if similarity >= 0.60:

                    # 4. CONSTRAINT: SCORE (Determine which item is retained)
                    if item_i["score"] >= item_j["score"]:
                        print(f"[BOUNDARY DEDUP] Dropped S{slice_j}: '{item_j['text']}' (score: {item_j['score']:.2f}) "
                              f"-> duplicate of S{slice_i}: '{item_i['text']}' (score: {item_i['score']:.2f}) (similarity: {similarity:.2f})")
                        to_remove.add(j)
                    else:
                        print(f"[BOUNDARY DEDUP] Dropped S{slice_i}: '{item_i['text']}' (score: {item_i['score']:.2f}) "
                              f"-> duplicate of S{slice_j}: '{item_j['text']}' (score: {item_j['score']:.2f}) (similarity: {similarity:.2f})")
                        to_remove.add(i)
                        break  # item_i was removed, break to proceed to the next 'i'

    final_items = [item for idx, item in enumerate(items) if idx not in to_remove]
    print(f"[BOUNDARY DEDUP] Removed {len(to_remove)} hard duplicates from slice boundaries (Geo + Text + Score).")
    return final_items