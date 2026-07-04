
# merge_boxes.py

"""
Provides space aware grouping and clustering utilities for OCR text items.

This module is responsible for aggregating individual OCR words into coherent
text lines, and subsequently grouping those lines into distinct speech bubbles
or text blocks. It addresses complex layout challenges typical in vertical
reading formats (e.g., mangas, webtoons), such as tightly packed or vertically
interleaved dialogue columns.

Core features:
1. Vertical Stream Detection: Uses a Union-Find algorithm to identify vertical
   columns of text. This acts as a geometric guard rail to prevent merging
   adjacent but physically distinct bubbles.
2. Line Construction: Groups words horizontally while dynamically splitting them
   based on physical gaps and stream IDs.
3. Bubble Clustering: Merges lines into bubbles using a best-match scoring system
   based on aggregate bounding box geometry, Intersection over Union (IoU), and
   containment metrics.
4. Noise Filtering: Identifies and removes standalone single-character OCR artifacts
   before downstream processing (e.g., inpainting, translation).
"""

from utils.text_utils import _SINGLE_CHAR_WHITELIST
from collections import Counter
from statistics import median
import string

# --- VERTICAL STREAMS DETECTION CONFIGURATION ---
# See: compute_vertical_streams(). These constants are not part of the global
# TEXT_THRESHOLD or pipeline config - they are purely local geometric thresholds.
_STREAM_WINDOW = 14          # Number of consecutive items (by cy (y - vertical)) considered when looking for a vertical neighbor
_STREAM_MAX_V_GAP = 150.0    # Max vertical gap [px] between items to be considered the same column
_STREAM_MIN_X_OVERLAP = 0.35 # Minimum X overlap ratio to consider items in the "same column"
_STREAM_MAX_CX_DRIFT = 0.6   # Fallback when X overlap is missing: allowed cx drift as a fraction of the wider box

# --- HELPER FUNCTIONS ---
def box_stats(box):
    # Force float64 to handle extremely long images (e.g., webtoons)
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
    Checks if two OCR items lie on the same text line.

    Uses solely the distance between their Y-centers (cy) — without vertical_overlap.

    Why vertical_overlap was removed:
    PaddleOCR often returns tall boxes (e.g., 60px) for a line spacing of ~28px,
    yielding ~53% overlap between adjacent lines. With overlap_min=0.35, this
    caused consecutive lines to merge via "chaining" — each new item was within
    y_tol of the previous one, dragging an entire vertical bubble into a single
    line, and subsequent x1-sorting reversed the correct reading order.

    y_tol = 30% of the smaller box's height (previously 40%):
    - for h=60px: y_tol=18px  -> correctly separates lines spaced by ~22–28px
    - for h=30px: y_tol=9px   -> safely merges items on the exact same line
    """
    if y_tol is None:
        y_tol = max(5.0, 0.3 * min(a["h"], b["h"]))
    return abs(a["cy"] - b["cy"]) <= y_tol


def join_gap(a, b):
    return max(0.0, b["x1"] - a["x2"])


# --- VERTICAL STREAMS DETECTION ---
#
# Problem being solved: When two different bubbles are close to each other on
# the X-axis and their text lines happen to fall at the exact same Y-height.
# No heuristic looking SOLELY at a single line can distinguish them if the
# horizontal gap between them is exceptionally small. Furthermore, with multiple
# columns of text interleaving on the Y-axis, comparing a new line only to the
# LAST added line of a bubble causes "drift", eventually gluing physically
# distinct bubbles together.
#
# VISUAL EXAMPLE OF THE "CLOSE GAP" PROBLEM:
#
#   Y=10:    [AN ORDER]          [EVEN IN A STATE]
#   Y=40:    [HAS BEEN]          [OF EMERGENCY]
#   Y=70:    [GIVEN TO THIS MAN]-[WE CANNOT]        <-- Danger Zone
#
# At Y=70, the left text line is wide, making the horizontal gap between
# "MAN" and "WE" extremely narrow. A naive line-builder looking only at the
# X-axis gap will mistakenly merge them into one horizontal line:
# "GIVEN TO THIS MAN WE CANNOT".
#
# Solution: An independent, line-agnostic geometric signal - "do these two items
# lie one below the other in roughly the same X column?" - calculated on individual
# OCR words, bypassing line segmentation. This naturally mirrors the physical shape
# of a speech bubble (a narrow, vertical column of text) and works perfectly
# regardless of X-axis spacing.
#
# VISUAL EXAMPLE OF THE SOLUTION (Streams):
#
#      Stream 1                     Stream 2
#     [AN ORDER]          !=    [EVEN IN A STATE]
#         | (linked)                    | (linked)
#     [HAS BEEN]                [OF EMERGENCY]
#         | (linked)                    | (linked)
#     [GIVEN TO THIS MAN]       [WE CANNOT]
#
# By grouping vertically first, "GIVEN..." is solidly anchored to Stream 1,
# and "WE CANNOT" is anchored to Stream 2. Now, even if they share the exact
# same Y=70 elevation with a tiny gap, the guard rail sees (Stream 1 != Stream 2)
# and FORCES a horizontal split, keeping the text bubbles completely separate.
#
# This DOES NOT replace same_line / split_line_by_gap / group_lines_into_bubbles -
# it is an additional guard rail used in both places: it forces a line split or
# rejects a bubble match when two items have a strong (confirmed, >=2 items) but
# DIFFERENT stream. Lack of evidence (a 1-item stream) is never treated as a
# "definitely different bubbles" signal - only an actual, confirmed column is.

class _UnionFind:
    """
    A lightweight Disjoint-Set (Union-Find) data structure with path compression.

    Used internally to efficiently cluster individual OCR text items into connected
    vertical streams (columns) by merging neighboring geometric components.
    """
    __slots__ = ("parent",)

    def __init__(self, n):
        """Initializes n independent disjoint sets."""
        self.parent = list(range(n))

    def find(self, x):
        """
        Finds the root representative of the set containing x.

        Applies path compression during traversal to flatten the tree structure,
        ensuring near constant time complexity [ O(α(n)) ] for future queries.
        """
        root = x
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[x] != root:
            self.parent[x], x = root, self.parent[x]
        return root

    def union(self, a, b):
        """Merges the disjoint sets containing elements a and b."""
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[ra] = rb


def _x_overlap_ratio(a, b):
    """
    Calculates the horizontal (X-axis) overlap ratio between two bounding boxes.

    The overlap is strictly calculated relative to the width of the *narrower* box.
    This is a deliberate geometric choice: if a short word (e.g., "I") is perfectly
    stacked above or below a wide word (e.g., "SOMETHING"), dividing by the smaller
    width yields a 1.0 (100%) overlap. This allows robust detection of vertical
    text columns even when line widths vary drastically.

    :param a: Spatial stats dictionary for the first bounding box.
    :param b: Spatial stats dictionary for the second bounding box.
    :return: A float between 0.0 and 1.0 representing the overlap ratio.
    """
    inter = max(0.0, min(a["x2"], b["x2"]) - max(a["x1"], b["x1"]))
    return inter / max(1.0, min(a["w"], b["w"]))


def _likely_same_column(top, bottom,
                        max_v_gap=_STREAM_MAX_V_GAP,
                        min_x_overlap=_STREAM_MIN_X_OVERLAP,
                        max_cx_drift=_STREAM_MAX_CX_DRIFT):
    """
    Determines if `bottom` (the lower item) appears to be a continuation of the
    same text column as `top` (the upper item). Both arguments are stats dicts.
    """
    v_gap = bottom["y1"] - top["y2"]
    if v_gap > max_v_gap:
        return False
    if v_gap < -min(top["h"], bottom["h"]) * 0.5:
        # Too much vertical overlap - this is likely the same row, not two separate
        # lines in a column. Not our concern here (same_line handles this).
        return False
    if _x_overlap_ratio(top, bottom) >= min_x_overlap:
        return True
    cx_drift = abs(top["cx"] - bottom["cx"])
    return cx_drift <= max_cx_drift * max(top["w"], bottom["w"])


def compute_vertical_streams(enriched_items, window=_STREAM_WINDOW):
    """
    Returns a dict mapping index in `enriched_items` -> stream_id (int) or None.

    A stream_id is assigned only when an item has at least one confirmed neighbor
    in the same column (stream size >= 2). Single, isolated items (e.g., the only
    line in a small bubble, with nothing above/below in a similar X) get None -
    lack of evidence is not evidence of a "different bubble", so such items
    should not force any improper splits.
    """
    n = len(enriched_items)
    if n == 0:
        return {}

    uf = _UnionFind(n)
    order = sorted(range(n), key=lambda i: enriched_items[i]["stats"]["cy"])

    for oi, i in enumerate(order):
        a = enriched_items[i]["stats"]
        for j in order[oi + 1: oi + 1 + window]:
            b = enriched_items[j]["stats"]
            top, bottom = (a, b) if a["cy"] <= b["cy"] else (b, a)
            if bottom["y1"] - top["y2"] > _STREAM_MAX_V_GAP:
                break  # Order is sorted by cy - it only gets further away from here
            if _likely_same_column(top, bottom):
                uf.union(i, j)

    roots = [uf.find(i) for i in range(n)]
    sizes = Counter(roots)
    return {idx: (root if sizes[root] >= 2 else None) for idx, root in enumerate(roots)}


def split_line_by_gap(line_items, gap_multiplier=3.0, image_width=None):
    """
    Splits a line into separate fragments if the horizontal gap between items is too large.
    Prevents merging words from different bubbles that happen to share the same Y elevation.

    VISUAL EXAMPLE: THE "IMAGE WIDTH CAP" PROBLEM

    Imagine a wide canvas (e.g., image_width = 1250px). We have two completely
    separate bubbles sharing the same Y-plane:

    x=130          x=250            x=390              x=490
      [THEN HOW]                         [TO STOP]
      (avg_w: 120px)     <GAP 140px>     (avg_w: 100px)

    Logic WITHOUT image_width cap:
      - avg_w ~ 110px.
      - max_gap = 110px * gap_multiplier (3.0) = 330px.
      - Actual gap (140px) < max_gap (330px) -> MERGED x (Wrong!)

    Logic WITH image_width cap (e.g., 5% of 1250px = 62.5px):
      - max_gap = min(330, 62.5) = 62.5px.
      - Actual gap (140px) > max_gap (62.5px) -> SPLIT - ok - (Correctly separated!)

    :param line_items:      List of OCR items belonging to a single line.
    :param gap_multiplier:  How many times wider than the average word the gap can be.
    :param image_width:     Width of the scaled image in pixels. Caps the allowed gap.
    :return: A list of lists — each sublist is a separate line fragment.
    """
    if len(line_items) <= 1:
        return [line_items]

    sorted_items = sorted(line_items, key=lambda x: x["stats"]["x1"])
    avg_w = sum(i["stats"]["w"] for i in sorted_items) / len(sorted_items)
    max_gap = avg_w * gap_multiplier

    # Absolute cap: words inside a bubble are usually spaced by ~10–40 px;
    # a gap >10% of the image width almost certainly indicates a different bubble.
    if image_width and image_width > 0:
        abs_cap = image_width * 0.05
        max_gap = min(max_gap, abs_cap)

    chunks = [[sorted_items[0]]]
    for item in sorted_items[1:]:
        prev = chunks[-1][-1]
        gap = item["stats"]["x1"] - prev["stats"]["x2"]

        # GUARD RAIL: if both items have a strong (confirmed, >=2 items) but
        # DIFFERENT vertical stream (see compute_vertical_streams) - it is highly
        # probable these are two different text columns / bubbles, even if the
        # horizontal gap is smaller than max_gap. This handles the exact case of
        # lines like "AN ORDER" + "EVEN IN A STATE": different bubbles, same Y row,
        # small horizontal gap - the gap alone won't catch this.
        prev_stream = prev.get("stream_id")
        item_stream = item.get("stream_id")
        different_confirmed_stream = (
                prev_stream is not None
                and item_stream is not None
                and prev_stream != item_stream
        )

        if gap > max_gap or different_confirmed_stream:
            chunks.append([item])
        else:
            chunks[-1].append(item)

    return chunks


# --- GROUPING FUNCTIONS ---
def build_text_lines(items, image_width=None):
    """
    Group individual OCR text items into cohesive horizontal lines without altering the original source data.
    Returns a list of dictionaries where each dictionary represents a consolidated text line.

    After performing a rough layout grouping based on the Y-axis, each line is conditionally split
    using `split_line_by_gap`. This spatial validation prevents accidentally merging words originating
    from separate text bubbles that happen to share the same horizontal elevation.

    :param image_width: Width of the scaled image in pixels. Passed down to `split_line_by_gap`.
    """
    if not items:
        return []

    # 1. Guarantee every unique input item possesses a designated, trackable identifier (ID)
    for idx, item in enumerate(items):
        if "id" not in item:
            item["id"] = f"word_{idx}"

    # 2. Extract space-aware sorting metrics for sorting (executed on a data copy to preserve pristine state of items)
    enriched = []
    for item in items:
        stats = box_stats(item["box"])
        enriched.append({**item, "stats": stats})

    # 3. Perform a rough vertical top-to-bottom sort based on centroid coordinates
    enriched.sort(key=lambda x: x["stats"]["cy"])

    # 3b. Detect vertical streams (text columns) - an independent geometric signal
    #     used later as a guard rail in split_line_by_gap (see compute_vertical_streams).
    stream_ids = compute_vertical_streams(enriched)
    for idx, item in enumerate(enriched):
        item["stream_id"] = stream_ids.get(idx)

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

    # 4. Partition each line via split_line_by_gap — acts as a patch for words spanning across distinct bubbles
    #    that reside on an identical vertical Y plane.
    lines = []
    for raw_line in raw_lines:
        raw_line.sort(key=lambda x: x["stats"]["x1"])
        chunks = split_line_by_gap(raw_line, image_width=image_width)
        lines.extend(chunks)

    grouped_lines = []

    # 5. Apply horizontal sorting and construct isolated data models for individual lines
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

        # Dominant stream_id of a row - majority of the votes of its component words.
        # None if no word has a confirmed stream (no evidence -> no signal).
        stream_counter = Counter(i["stream_id"] for i in line if i.get("stream_id") is not None)
        line_stream_id = stream_counter.most_common(1)[0][0] if stream_counter else None

        grouped_lines.append({
            "line_id": f"line_{line_idx}",
            "word_ids": word_ids,
            "text": merged_text,
            "score": avg_score,
            "box": merged_box,
            "stream_id": line_stream_id,
        })

    return grouped_lines


def _group_stats(b_group):
    """
    Returns aggregate space-aware stats for an entire bubble group (all lines combined).

    Using the WHOLE group bounding box instead of just the last line is the core fix
    for the bubble_6/7/9 merging bug: when three interleaved columns of text share the
    same Y range, comparing only to b_group[-1] causes "drift" — each new line from a
    different column is close to the previous one, so they all end up in the same group.
    Comparing to the aggregate box makes the accumulated X range visible and lets the
    cx / x_overlap checks reject truly distinct columns.
    """
    all_points = []
    for line in b_group:
        all_points.extend(line["box"])
    agg = box_stats(all_points)

    # Median cx of individual lines — more robust than the aggregate centroid for
    # groups where a single wide line would skew the center.
    median_cx = median(box_stats(l["box"])["cx"] for l in b_group)
    agg["median_cx"] = median_cx
    return agg


def _score_candidate(l_stats, b_stats, y_dist_mult, x_overlap_thresh):
    """
    Evaluates how well a line fits into a bubble group.

    Returns a float score >= 0 if the line is a valid candidate (lower = better fit),
    or None if any hard constraint is violated and the line must NOT join this group.

    Hard constraints (return None):
      - vertical gap too large
      - x overlap ratio below threshold
      - cx distance too large
      - confirmed different vertical stream (stream_id guard rail)

    Score (when all constraints pass):
      Combination of normalized vertical gap and cx distance — lower means the line
      is geometrically closer to this group's center.
    """
    vertical_gap = l_stats["y1"] - b_stats["y2"]

    abs_gap_limit = 120.0
    max_gap = min(abs_gap_limit, b_stats["h"] * y_dist_mult)
    if vertical_gap >= max_gap:
        return None

    overlap_x = max(0.0, min(l_stats["x2"], b_stats["x2"]) - max(l_stats["x1"], b_stats["x1"]))

    # X alignment check — two complementary metrics, take the higher one.
    #
    # VISUAL EXAMPLE OF X-ALIGNMENT METRICS (IoU vs Containment):
    #
    # CASE 1: Narrow word near a wide bubble (Slight X-overlap but different bubbles)
    #
    #           [ I ](w:15)
    #               [ SOMETHING ELSE ENTIRELY ](w:200)
    #
    #   -> IoU is very low (~0.07). REJECTED. (Correct!)
    #
    # CASE 2: Narrow word genuinely inside a wide bubble group
    #
    #                     [  VR  ](w:30)
    #           [ VERY WIDE BUBBLE GROUP BOX  ](w:200)
    #
    #   -> IoU is STILL very low (~0.15) -> WOULD BE FALSELY REJECTED!
    #   -> Containment ratio (Overlap / Line_Width): 30/30 = 1.0 -> ACCEPTED. (Correct!)
    #
    # Containment is used only when the line is significantly narrower than the
    # group (width ratio < 0.4). In all other cases, IoU is the primary metric.

    iou_x = overlap_x / max(1.0, l_stats["w"] + b_stats["w"] - overlap_x)
    containment_x = overlap_x / max(1.0, l_stats["w"])
    containment_group = overlap_x / max(1.0, b_stats["w"])

    # Use a more liberal containment metric when ANY side is significantly
    # narrower than the other (ratio < 0.4). This handles two symmetric cases:
    #   - narrow line ("VR") inside a wide group -> containment_x saves it
    #   - wide line ("FINE DAY") trying to join a group anchored by a narrow word
    #     ("A") → containment_group saves it
    # The cx_dist guard below still rejects cross-bubble false positives.
    narrow_line = l_stats["w"] < b_stats["w"] * 0.4
    narrow_group = b_stats["w"] < l_stats["w"] * 0.4
    if narrow_line or narrow_group:
        x_overlap_ratio = max(iou_x, containment_x, containment_group)
    else:
        x_overlap_ratio = iou_x

    if x_overlap_ratio <= x_overlap_thresh:
        return None

    cx_dist = abs(l_stats["cx"] - b_stats.get("median_cx", b_stats["cx"]))
    max_cx_dist = (l_stats["w"] + b_stats["w"]) * 0.45
    if cx_dist >= max_cx_dist:
        return None

    # Lower score = better fit; weight vertical proximity more than horizontal drift
    score = (vertical_gap / max(1.0, max_gap)) * 0.6 + (cx_dist / max(1.0, max_cx_dist)) * 0.4
    return score


def group_lines_into_bubbles(grouped_lines, x_overlap_thresh=0.35, y_dist_mult=1.5, min_score=0.7):
    """
    Groups individual text lines into speech bubble clusters using spatial constraints.

    VISUAL EXAMPLES OF THE LAYOUT PROBLEMS SOLVED:

    Problem 1: Overlaping/mixed Columns (Solved by BEST-MATCH)
      Group 1 (X:100)          Group 2 (X:300)
      [HELLO]                  [WHAT ARE]
      [THERE]                  [YOU DOING]
                      [TODAY?] <-- Incoming line (X:280)

      With FIRST-MATCH, [TODAY?] might pass the loose maximum-distance checks for
      Group 1 and be swallowed incorrectly. BEST-MATCH scores both candidate groups
      and correctly assigns it to Group 2 because the geometric distance is much smaller.

    Problem 2: The "Drift" Effect (Solved by AGGREGATE GROUP STATS)
      [LINE 1]
         [LINE 2]
            [LINE 3]
                              [LINE 4] <-- Different bubble

      If Group 1's bounds are based ONLY on the last appended line ([LINE 3]),
      the horizontal gap to [LINE 4] looks dangerously small. By using AGGREGATE
      STATS (a single bounding box wrapping Lines 1+2+3), the group's true center
      (median_cx) stays safely anchored to the left, preventing the bubble from
      "drifting" rightward and falsely swallowing Line 4.

    Key improvements over the original implementation:

    1. BEST-MATCH instead of FIRST-MATCH:
       The original code appended each line to the first bubble group that passed all
       checks. With overlapping columns (three bubbles sharing the same Y range), the
       first passing group was often the wrong one. Now every candidate group is scored
       and the line goes to the geometrically closest match.

    2. AGGREGATE GROUP STATS instead of LAST-LINE STATS:
       Spatial checks (vertical gap, x overlap, cx distance) are now computed against
       the full bounding box of the ENTIRE group, not just its last appended line.
       See _group_stats() for rationale.

    3. VERTICAL STREAM GUARD RAIL:
       If the incoming line has a confirmed stream_id (set by compute_vertical_streams,
       meaning >=2 words stacked vertically in the same X column) AND the dominant
       stream_id of the candidate group is confirmed AND they differ — the line is
       forbidden from joining that group regardless of distance scores. This fires
       exactly when columns are close in X but geometrically distinct (the line_45 case).
    """
    if not grouped_lines:
        return []

    # 1. Discard low-confidence recognition noise from entering the pipeline
    lines = [l for l in grouped_lines if l["score"] >= min_score]
    if not lines:
        return []

    lines = sorted(lines, key=lambda l: box_stats(l["box"])["cy"])

    # Each entry: list of line dicts, plus a cached dominant stream_id for the group.
    # stream_id is recomputed as majority vote when a new line joins.
    bubbles_list = []  # list of {"lines": [...], "stream_id": int|None}

    for line in lines:
        l_stats = box_stats(line["box"])
        l_stream = line.get("stream_id")

        best_score = None
        best_idx = None

        for bi, entry in enumerate(bubbles_list):
            b_group = entry["lines"]
            b_stream = entry["stream_id"]

            # GUARD RAIL: confirmed, different vertical stream → hard reject
            if (l_stream is not None and b_stream is not None and l_stream != b_stream):
                continue

            b_stats = _group_stats(b_group)
            score = _score_candidate(l_stats, b_stats, y_dist_mult, x_overlap_thresh)
            if score is None:
                continue

            if best_score is None or score < best_score:
                best_score = score
                best_idx = bi

        if best_idx is not None:
            bubbles_list[best_idx]["lines"].append(line)

            # Update dominant stream_id for the group (majority vote)
            stream_counter = Counter(
                l.get("stream_id") for l in bubbles_list[best_idx]["lines"]
                if l.get("stream_id") is not None
            )
            bubbles_list[best_idx]["stream_id"] = (
                stream_counter.most_common(1)[0][0] if stream_counter else None
            )
        else:
            bubbles_list.append({"lines": [line], "stream_id": l_stream})

    # 2. Build output bubble dicts
    merged_bubbles = []
    for idx, entry in enumerate(bubbles_list):
        b_group = entry["lines"]

        # b_group is sorted by cy from the loop above (lines were sorted before processing)
        combined_text = " ".join(l["text"] for l in b_group)

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
            "avg_score": sum(l["score"] for l in b_group) / len(b_group),
        })

    return merged_bubbles


def is_noise_text(text: str) -> bool:
    """
    Core validation logic to detect standalone single-letter OCR noise.

    Trims whitespace and punctuation to evaluate the actual text content.
    Returns True if the text consists of a single character that is NOT
    present in the allowed single-character whitelist.
    """
    clean = text.strip().strip(string.punctuation)
    return len(clean) == 1 and clean.upper() not in _SINGLE_CHAR_WHITELIST


def filter_noise_lines(lines: list[dict]) -> list[dict]:
    """
    Remove single-letter artifacts at the individual line level.

    This filter runs BEFORE the inpainting step to prevent the pipeline
    from erasing and damaging background graphics due to OCR noise.
    """
    return [line for line in lines if not is_noise_text(line["text"])]


def filter_noise_bubbles(bubbles: list[dict]) -> list[dict]:
    """
    Remove standalone single-letter noise from text blocks/bubbles.

    This filter runs BEFORE the translation phase to prevent the LLM
    from processing meaningless character noise and causing hallucinations.
    """
    return [b for b in bubbles if not is_noise_text(b["text"])]