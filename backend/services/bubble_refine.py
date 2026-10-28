
# backend/services/bubble_refine.py

"""
Fixes for two OCR weaknesses, both found by looking at the bubble the OCR text sits in (utils/bubble_interior.py):

  1. A bubble CUT INTO PIECES. The OCR finds text lines, and bubbles are built afterwards by grouping lines; when
     lines are missing or far apart, one speech bubble comes out as two or three "bubbles". If the interiors of two
     neighbouring bubbles are one and the same outlined white area, they are merged.
  2. A bubble that is only HALF READ (lines 1, 2, 4, 6 found, 3 and 5 not). The analysis in utils/inpainting.analyze_bubble
     sees dark pixels inside the bubble that no OCR box explains ("missed_text"); the whole interior is then OCR'd
     again as ONE crop - bigger text on a clean background - and the result replaces the bubble's lines when it is
     clearly more complete (keeps most of the old words, has more of them).

The same region-reading is used by the editor's manual actions (services/bubble_edit.py): "Re-OCR bubble" and
"Add bubble" (draw a rectangle over text OCR never saw).

Everything works in ONE coordinate space (whatever the image passed in uses), ocr_region_fn(image, bbox) -> items
[{"text", "score", "box"}] in that same space.
"""

from rapidfuzz import fuzz

from utils.bubble_interior import find_interior
from utils.inpainting import analyze_bubble, MIN_WHITE_BRIGHTNESS
from utils.merge_boxes import build_text_lines


# ------------------------------------------------------------------ geometry helpers

def bbox_of(box) -> tuple[float, float, float, float]:
    xs = [p[0] for p in box]
    ys = [p[1] for p in box]
    return min(xs), min(ys), max(xs), max(ys)


def union_bbox(a, b):
    return min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])


def rect_poly(bbox) -> list[list[float]]:
    x1, y1, x2, y2 = bbox
    return [[x1, y1], [x2, y1], [x2, y2], [x1, y2]]


def center(bbox) -> tuple[float, float]:
    return (bbox[0] + bbox[2]) / 2.0, (bbox[1] + bbox[3]) / 2.0


def expand(bbox, ratio: float, limit_w: int, limit_h: int, min_pad: int = 6):
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    px, py = max(min_pad, int(w * ratio)), max(min_pad, int(h * ratio))
    return (max(0, int(bbox[0] - px)), max(0, int(bbox[1] - py)), min(limit_w, int(bbox[2] + px)), min(limit_h, int(bbox[3] + py)))


def _norm_words(text: str) -> list[str]:
    return ["".join(c for c in w.lower() if c.isalnum()) for w in text.split() if any(c.isalnum() for c in w)]


# ------------------------------------------------------------------ reading a region as ONE bubble

def read_region(image, bbox, ocr_region_fn) -> dict | None:
    """
    OCR a rectangle and treat everything found there as the lines of a single bubble.
    :return: None when nothing was read, else
             {"text", "box" (union of lines), "avg_score", "lines": [{"box", "text", "score"}], "items": [...]}
    """
    items = ocr_region_fn(image, bbox) or []
    if not items:
        return None
    for i, it in enumerate(items):
        it["id"] = f"rw_{i}"
    lines = build_text_lines(items, image_width=image.shape[1])
    if not lines:
        return None
    lines.sort(key=lambda l: (round(bbox_of(l["box"])[1] / 8), bbox_of(l["box"])[0]))   # top to bottom, left to right
    box = lines[0]["box"]
    union = bbox_of(box)
    for l in lines[1:]:
        union = union_bbox(union, bbox_of(l["box"]))
    return {
        "text": " ".join(l["text"] for l in lines),
        "box": rect_poly(union),
        "avg_score": sum(l["score"] for l in lines) / len(lines),
        "lines": [{"box": l["box"], "text": l["text"], "score": l["score"]} for l in lines],
        "items": items,
    }


def _more_complete(old_text: str, new_text: str) -> bool:
    """New reading is accepted only if it has MORE words and still contains most of the old ones."""
    old, new = _norm_words(old_text), _norm_words(new_text)
    if len(new) <= len(old) or not old:
        return len(new) > len(old)
    kept = sum(1 for w in old if any(fuzz.ratio(w, v) >= 80 for v in new))
    return kept / len(old) >= 0.6


# ------------------------------------------------------------------ pipeline step (result dict from process_image)

def _next_line_number(result: dict) -> int:
    nums = [int(l["line_id"].split("_")[-1]) for l in result["lines"] if l["line_id"].split("_")[-1].isdigit()]
    return max(nums, default=-1) + 1


def _bubble_target(result: dict, bubble: dict) -> dict:
    by_id = {l["line_id"]: l for l in result["lines"]}
    return {"bubble_id": bubble["bubble_id"], "bbox": bbox_of(bubble["box_coords"]),
            "boxes": [by_id[i]["box"] for i in bubble.get("line_ids", []) if i in by_id]}


def merge_split_bubbles(image, result: dict, max_growth: float = 2.2) -> int:
    """Merge bubbles whose texts lie in one and the same outlined white area. Returns how many bubbles disappeared."""
    bubbles = result["bubbles"]
    n = len(bubbles)
    if n < 2:
        return 0
    boxes = [bbox_of(b["box_coords"]) for b in bubbles]
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i in range(n):
        for j in range(i + 1, n):
            a, b = boxes[i], boxes[j]
            gap_x = max(0.0, max(a[0], b[0]) - min(a[2], b[2]))
            gap_y = max(0.0, max(a[1], b[1]) - min(a[3], b[3]))
            wa, wb, ha, hb = a[2] - a[0], b[2] - b[0], a[3] - a[1], b[3] - b[1]
            if gap_x > 0.5 * min(wa, wb) or gap_y > 2.5 * max(ha, hb):
                continue                                          # too far apart to be pieces of one bubble
            if find(i) == find(j):
                continue
            u = union_bbox(a, b)
            it = find_interior(image, center(a), u, max_growth=max_growth)
            if it is not None and it.contains(*center(a)) and it.contains(*center(b)):
                parent[find(j)] = find(i)

    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    if all(len(g) == 1 for g in groups.values()):
        return 0

    by_line = {l["line_id"]: l for l in result["lines"]}
    merged = []
    for members in groups.values():
        if len(members) == 1:
            merged.append(bubbles[members[0]])
            continue
        parts = [bubbles[i] for i in members]
        line_ids = [lid for p in parts for lid in p.get("line_ids", [])]
        lines = sorted((by_line[i] for i in line_ids if i in by_line), key=lambda l: (bbox_of(l["box"])[1], bbox_of(l["box"])[0]))
        total_lines = sum(p["line_count"] for p in parts)
        union = bbox_of(parts[0]["box_coords"])
        for p in parts[1:]:
            union = union_bbox(union, bbox_of(p["box_coords"]))
        merged.append({
            "bubble_id": parts[0]["bubble_id"],
            "text": " ".join(l["text"] for l in lines) if lines else " ".join(p["text"] for p in parts),
            "box_coords": rect_poly(union),
            "line_count": total_lines,
            "line_ids": [l["line_id"] for l in lines] or line_ids,
            "avg_score": sum(p["avg_score"] * p["line_count"] for p in parts) / max(1, total_lines),
            "merged_from": len(parts),
        })
    merged.sort(key=lambda b: center(bbox_of(b["box_coords"]))[1])
    for idx, b in enumerate(merged):                           # same numbering scheme as group_lines_into_bubbles
        b["bubble_id"] = f"bubble_{idx}"
    removed = n - len(merged)
    result["bubbles"] = merged
    return removed


def refine_missed_text(image, result: dict, ocr_region_fn) -> int:
    """Re-OCR bubbles with text pixels no OCR box explains. Returns how many bubbles got a fuller reading."""
    refined = 0
    ih, iw = image.shape[:2]
    for bubble in result["bubbles"]:
        target = _bubble_target(result, bubble)
        it, info, _ = analyze_bubble(image, target)
        bubble["missed_text"] = bool(info["missed_text"])
        if it is None or not info["missed_text"] or it.brightness < MIN_WHITE_BRIGHTNESS:
            continue
        region = expand(it.bbox, 0.02, iw, ih)
        reading = read_region(image, region, ocr_region_fn)
        if reading is None or not _more_complete(bubble["text"], reading["text"]):
            continue

        base = _next_line_number(result)
        old_ids = set(bubble.get("line_ids", []))
        old_word_ids = {w for l in result["lines"] if l["line_id"] in old_ids for w in l.get("word_ids", [])}
        new_lines, new_ids = [], []
        for k, l in enumerate(reading["lines"]):
            lid = f"line_{base + k}"
            new_ids.append(lid)
            new_lines.append({"line_id": lid, "word_ids": [], "text": l["text"], "score": l["score"], "box": l["box"], "stream_id": None})
        result["lines"] = [l for l in result["lines"] if l["line_id"] not in old_ids] + new_lines
        result["items"] = [i for i in result["items"] if i.get("id") not in old_word_ids] + reading["items"]
        bubble.update(text=reading["text"], box_coords=reading["box"], line_count=len(new_lines), line_ids=new_ids,
                      avg_score=reading["avg_score"], refined=True)
        # re-analyse with the new boxes: the leftover should be gone now
        _, info2, _ = analyze_bubble(image, _bubble_target(result, bubble))
        bubble["missed_text"] = bool(info2["missed_text"])
        refined += 1
    return refined


def refine_bubbles(image, result: dict, ocr_region_fn, merge: bool = True, reread: bool = True) -> dict:
    stats = {"merged": 0, "refined": 0}
    if merge:
        stats["merged"] = merge_split_bubbles(image, result)
    if reread:
        stats["refined"] = refine_missed_text(image, result, ocr_region_fn)
    return stats
