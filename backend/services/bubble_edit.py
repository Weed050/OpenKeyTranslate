
# backend/services/bubble_edit.py

"""
Manual bubble operations on a page's saved JSON (the editor's "Re-OCR bubble", "Add bubble", "Merge" - any number
of bubbles, picked with Shift+click on the page - and "Split" - lines of one bubble into two, translation divided):
pure functions on the ocr_data dict + image + an OCR function, so they are testable without a server.

JSON shape they work with (services/page_export.save_ocr_json):
  bubbles[]: {bubble_id, text, translation, ai_translation, box (4 points), line_count, avg_score,
              [text_raw, skip, skip_reason, interior_box, missed_text, refined]}
  lines[]:   {line_id, box, bubble_id}
All coordinates are original-image coordinates.
"""

import re

from core.config import get_setting
from services.bubble_refine import bbox_of, center, expand, read_region, rect_poly, union_bbox
from utils.bubble_interior import find_interior
from utils.inpainting import analyze_bubble
from utils.ocr_textfix import fix_ocr_text, skip_reason


class BubbleEditError(Exception):
    """User-facing (message is shown as is); .status is the HTTP status the router should use."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _find(ocr_data: dict, bubble_id: str) -> dict:
    for b in ocr_data.get("bubbles", []):
        if b["bubble_id"] == bubble_id:
            return b
    raise BubbleEditError("Bubble not found on this page", 404)


def _next_number(ids, prefix: str) -> int:
    nums = [int(i.split("_")[-1]) for i in ids if i.startswith(prefix) and i.split("_")[-1].isdigit()]
    return max(nums, default=-1) + 1


def _replace_lines(ocr_data: dict, bubble_id: str, new_lines: list[dict]) -> None:
    if "lines" not in ocr_data:
        return       # page processed before line data existed: creating a partial list would make a rebuild bring the old text back
    lines = ocr_data["lines"]
    base = _next_number([l["line_id"] for l in lines], "line_")
    kept = [l for l in lines if l.get("bubble_id") != bubble_id]
    for k, l in enumerate(new_lines):
        kept.append({"line_id": f"line_{base + k}", "box": [[round(x, 1), round(y, 1)] for x, y in l["box"]], "bubble_id": bubble_id,
                     "text": l.get("text", ""), **({"angle": l["angle"]} if "angle" in l else {})})
    ocr_data["lines"] = kept


def _target(ocr_data: dict, bubble: dict) -> dict:
    boxes = [l["box"] for l in ocr_data.get("lines", []) if l.get("bubble_id") == bubble["bubble_id"]]
    return {"bubble_id": bubble["bubble_id"], "bbox": bbox_of(bubble["box"]), "boxes": boxes}


def _refresh_info(image, ocr_data: dict, bubble: dict) -> None:
    if "lines" not in ocr_data:
        return       # no text boxes to compare against -> the analysis would flag every bubble
    _, info, _ = analyze_bubble(image, _target(ocr_data, bubble))
    bubble["interior_box"] = info["interior_box"]
    bubble["missed_text"] = bool(info["missed_text"])


def _clean(text: str, protected) -> str:
    return fix_ocr_text(text, protected) if get_setting("ocr_textfix", True) else text


def _reading_region(image, bubble: dict) -> tuple:
    """The whole bubble interior when it can be found, else the bubble box with some margin."""
    ih, iw = image.shape[:2]
    bbox = bbox_of(bubble["box"])
    it = find_interior(image, center(bbox), bbox)
    return expand(it.bbox if it else bbox, 0.02 if it else 0.3, iw, ih)


def reocr_bubble(ocr_data: dict, bubble_id: str, image, ocr_region_fn, protected=(), box=None) -> dict:
    """
    Read the bubble's whole region again as one crop and replace its text + lines. The old text goes to text_raw.
    `box` = [x1, y1, x2, y2] drawn by the user: that exact area is read (for a bubble whose detected box misses a line,
    e.g. text below the box); without it the region is the bubble interior (flood fill) or the bubble box + margin.
    """
    bubble = _find(ocr_data, bubble_id)
    if box is not None:
        ih, iw = image.shape[:2]
        x1, y1, x2, y2 = (float(v) for v in box)
        x1, x2 = sorted((max(0.0, x1), min(float(iw), x2)))
        y1, y2 = sorted((max(0.0, y1), min(float(ih), y2)))
        if x2 - x1 < 12 or y2 - y1 < 12:
            raise BubbleEditError("The selected area is too small.", 400)
        region = (int(x1), int(y1), int(x2), int(y2))
    else:
        region = _reading_region(image, bubble)
    reading = read_region(image, region, ocr_region_fn)
    if reading is None:
        raise BubbleEditError("No text was found in this bubble's area.", 422)

    new_text = _clean(reading["text"], protected)
    changed = new_text != bubble["text"]
    if changed:
        bubble.setdefault("text_raw", bubble["text"])
        bubble["text"] = new_text
    bubble.update(box=[[round(x, 1), round(y, 1)] for x, y in reading["box"]], line_count=len(reading["lines"]),
                  avg_score=round(reading["avg_score"], 4))
    bubble.pop("refined", None)
    _replace_lines(ocr_data, bubble_id, reading["lines"])
    reason = skip_reason(bubble["text"], skip_symbols=get_setting("ocr_skip_symbols", True))
    if reason and not (reason == "number" and not get_setting("ocr_skip_numeric", True)) and not bubble.get("skip"):
        bubble["skip"], bubble["skip_reason"] = True, reason
    _refresh_info(image, ocr_data, bubble)
    return {"bubble": bubble, "text_changed": changed}


def add_bubble(ocr_data: dict, box, image, ocr_region_fn, protected=()) -> dict:
    """OCR a rectangle the user drew and add its content as a NEW bubble (text OCR never saw)."""
    ih, iw = image.shape[:2]
    x1, y1, x2, y2 = (float(v) for v in box)
    x1, x2 = sorted((max(0.0, x1), min(float(iw), x2)))
    y1, y2 = sorted((max(0.0, y1), min(float(ih), y2)))
    if x2 - x1 < 12 or y2 - y1 < 12:
        raise BubbleEditError("The selected area is too small.", 400)
    reading = read_region(image, (x1, y1, x2, y2), ocr_region_fn)
    if reading is None:
        raise BubbleEditError("No text was found in the selected area.", 422)

    bubbles = ocr_data.setdefault("bubbles", [])
    bubble_id = f"bubble_{_next_number([b['bubble_id'] for b in bubbles], 'bubble_')}"
    text = _clean(reading["text"], protected)
    bubble = {
        "bubble_id": bubble_id, "text": text, "translation": "", "ai_translation": "",
        "box": [[round(x, 1), round(y, 1)] for x, y in reading["box"]], "line_count": len(reading["lines"]),
        "avg_score": round(reading["avg_score"], 4), "added_by_user": True,
    }
    if text != reading["text"]:
        bubble["text_raw"] = reading["text"]
    reason = skip_reason(text, skip_symbols=get_setting("ocr_skip_symbols", True))
    if reason and not (reason == "number" and not get_setting("ocr_skip_numeric", True)):
        bubble["skip"], bubble["skip_reason"] = True, reason

    cy = center(bbox_of(bubble["box"]))[1]
    index = next((i for i, b in enumerate(bubbles) if center(bbox_of(b["box"]))[1] > cy), len(bubbles))   # keep top-to-bottom order
    bubbles.insert(index, bubble)
    _replace_lines(ocr_data, bubble_id, reading["lines"])
    _refresh_info(image, ocr_data, bubble)
    return {"bubble": bubble, "index": index}


def _merge_parts(ocr_data: dict, bubble_id: str, other_ids, order=None) -> list[dict]:
    """
    The bubbles to merge, in the order their texts are joined. Default = the reading-order setting (bubble_order:
    ttb / rtl / ltr) applied to just these pieces, so two pieces side by side in a right-to-left manga join right piece
    first. `order` (every id exactly once, from the editor's merge preview) overrides it.
    """
    others = [other_ids] if isinstance(other_ids, str) else list(other_ids or [])
    ids = [bubble_id] + [i for i in dict.fromkeys(others) if i != bubble_id]
    if len(ids) < 2:
        raise BubbleEditError("Choose at least two different bubbles.", 400)
    parts = {i: _find(ocr_data, i) for i in ids}
    if order:
        if sorted(order) != sorted(ids):
            raise BubbleEditError("order must list every merged bubble exactly once", 400)
        return [parts[i] for i in order]
    return auto_order(list(parts.values()), get_setting("bubble_order", "ttb"))


def _joined(parts: list[dict], key: str) -> str:
    return " ".join(p[key].strip() for p in parts if (p.get(key) or "").strip())


def _union_of(parts: list[dict]):
    union = bbox_of(parts[0]["box"])
    for p in parts[1:]:
        union = union_bbox(union, bbox_of(p["box"]))
    return union


def merge_preview(ocr_data: dict, bubble_id: str, other_ids, order=None) -> dict:
    """
    What merge_bubbles would produce, without changing anything (the editor's merge dialog). `covered` = bubbles NOT
    being merged whose centre lies inside the merged box - the merged translation would be typeset over them.
    """
    parts = _merge_parts(ocr_data, bubble_id, other_ids, order)
    ids = [p["bubble_id"] for p in parts]
    x1, y1, x2, y2 = _union_of(parts)
    covered = [b["bubble_id"] for b in ocr_data.get("bubbles", []) if b["bubble_id"] not in ids
               and x1 <= center(bbox_of(b["box"]))[0] <= x2 and y1 <= center(bbox_of(b["box"]))[1] <= y2]
    return {"order": ids, "text": _joined(parts, "text"), "translation": _joined(parts, "translation"), "covered": covered}


def merge_bubbles(ocr_data: dict, bubble_id: str, other_ids, image=None, order=None) -> dict:
    """
    Merge one or more bubbles INTO `bubble_id` (one speech bubble the OCR cut into pieces; the pieces need not be
    neighbours in the list). Texts and translations are joined in reading order (see _merge_parts). The merged bubble
    keeps `bubble_id` and takes the earliest list position of the pieces. Its "reviewed" stamp is dropped: the joined
    translation is new text nobody has confirmed yet.
    """
    parts = _merge_parts(ocr_data, bubble_id, other_ids, order)
    a = _find(ocr_data, bubble_id)
    removed = [p["bubble_id"] for p in parts if p["bubble_id"] != bubble_id]
    first_index = min(i for i, x in enumerate(ocr_data["bubbles"]) if x["bubble_id"] in removed or x is a)

    text_raw = (" ".join((p.get("text_raw") or p.get("text") or "").strip() for p in parts).strip()
                if any(p.get("text_raw") for p in parts) else None)      # a piece without text_raw: its text IS the raw OCR
    a.update(text=_joined(parts, "text"), translation=_joined(parts, "translation"),
             ai_translation=_joined(parts, "ai_translation"), box=rect_poly(_union_of(parts)),
             line_count=sum(p.get("line_count", 0) for p in parts))
    if text_raw:
        a["text_raw"] = text_raw
    for key in ("skip", "skip_reason", "confirmed_translation", "confirmed_at", "layout"):   # layout: the area changed
        a.pop(key, None)
    a["merged_by_user"] = True
    for line in ocr_data.get("lines", []):
        if line.get("bubble_id") in removed:
            line["bubble_id"] = bubble_id
    rest = [x for x in ocr_data["bubbles"] if x["bubble_id"] not in removed and x is not a]
    rest.insert(first_index, a)
    ocr_data["bubbles"] = rest
    if image is not None:
        _refresh_info(image, ocr_data, a)
    return {"bubble": a, "removed": removed[0] if len(removed) == 1 else removed, "removed_ids": removed,
            "order": [p["bubble_id"] for p in parts]}


# ------------------------------------------------------------------ split

_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+")


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_END.split((text or "").strip()) if s.strip()]


# English word that opens a piece -> Polish words the translation of that piece usually starts with
_LINKS = {
    "BUT": ("ALE", "LECZ", "JEDNAK", "A"), "AND": ("I", "A", "ORAZ"), "OR": ("ALBO", "LUB", "CZY"),
    "SO": ("WIĘC", "TO", "DLATEGO", "TAK"), "BECAUSE": ("BO", "PONIEWAŻ", "GDYŻ"), "SINCE": ("SKORO", "PONIEWAŻ", "OD"),
    "IF": ("JEŚLI", "JEŻELI", "GDY", "GDYBY"), "WHEN": ("GDY", "KIEDY"), "THEN": ("TO", "WTEDY", "POTEM"),
    "THAT": ("ŻE", "KTÓRY", "KTÓRA", "KTÓRE"), "WHICH": ("KTÓRY", "KTÓRA", "KTÓRE", "CO"), "WHO": ("KTÓRY", "KTÓRA", "KTO"),
    "ALTHOUGH": ("CHOĆ", "CHOCIAŻ", "MIMO"), "UNTIL": ("AŻ", "DOPÓKI"), "WHILE": ("PODCZAS", "GDY", "A"),
}
_CLAUSE_CUT = re.compile(r"(?<=[,;:–—-])\s+|\s+(?=[–—-]\s)")


def _clause_split(text: str, piece_sources: list[str]) -> list[str] | None:
    """
    One-sentence translation, several pieces ("... AT 9:55" | "BUT SINCE THERE IS SOME FREE TIME" ->
    "..., ALE PONIEWAŻ ..."): cut at clause boundaries (comma, semicolon, dash). A boundary whose next word is a
    Polish counterpart of the word opening the next source piece (BUT -> ALE) wins; otherwise the boundary whose
    share of the text is closest to the piece's share of the source. None when there are too few boundaries.
    """
    n = len(piece_sources)
    spots = [m.start() for m in _CLAUSE_CUT.finditer(text)]
    if len(spots) < n - 1:
        return None
    total = max(1, sum(max(1, len(p)) for p in piece_sources))
    cuts, prev = [], 0
    for k in range(1, n):
        share = sum(max(1, len(p)) for p in piece_sources[:k]) / total
        opener = (piece_sources[k].split() or [""])[0].strip(".,!?\"'").upper()
        links = _LINKS.get(opener, ())
        left = [p for p in spots if p > prev and len([q for q in spots if q > p]) >= n - 1 - k]
        if not left:
            return None
        def score(p):
            nxt = text[p:].lstrip(" ,;:-–—").split(" ", 1)[0].strip(".,!?\"'").upper()
            return (0 if links and nxt in links else 1, abs(p / len(text) - share))
        cut = min(left, key=score)
        cuts.append(cut)
        prev = cut
    bounds = [0] + cuts + [len(text)]
    parts = [text[a:b].strip() for a, b in zip(bounds, bounds[1:])]
    parts = [p.rstrip(" ,;:–—-").strip() for p in parts[:-1]] + parts[-1:]   # the cut comma does not end a bubble
    return parts if all(parts) else None


def split_translation(text: str, piece_sources: list[str]) -> tuple[list[str], str]:
    """
    Divide an existing translation between the pieces of a split bubble, in order, WITHOUT asking the translator:
      "sentences"    the translation has as many sentences as the source pieces together: each piece gets the same
                     number of sentences as its source text has ("WHAT... IS THIS?!" | "I DON'T KNOW." -> 2 | 1)
      "proportional" otherwise, with more sentences than pieces: cut between sentences where the share of the text
                     best matches the share of the source text of each piece
      "clauses"      fewer sentences than pieces: cut at commas / dashes, preferring a Polish conjunction that matches
                     the word opening the next source piece (BUT -> ALE), see _clause_split
      "unsplit"      not even that: the whole translation stays with the first piece, the rest is empty
    The editor marks "proportional" / "clauses" / "unsplit" pieces "check translation"; Retranslate is up to the user.
    """
    n = len(piece_sources)
    text = (text or "").strip()
    if n < 2 or not text:
        return [text] + [""] * max(0, n - 1), "sentences" if n < 2 else "unsplit"
    sents = _sentences(text)
    if len(sents) < n:
        clauses = _clause_split(text, piece_sources)
        return (clauses, "clauses") if clauses else ([text] + [""] * (n - 1), "unsplit")
    counts = [len(_sentences(s)) for s in piece_sources]
    if all(counts) and sum(counts) == len(sents):
        bounds = [sum(counts[:k]) for k in range(n + 1)]
        return [" ".join(sents[a:b]) for a, b in zip(bounds, bounds[1:])], "sentences"
    src = [max(1, len(s)) for s in piece_sources]
    targets = [sum(src[:k]) / sum(src) for k in range(1, n)]
    lengths = [len(s) + 1 for s in sents]
    shares = [sum(lengths[:i]) / sum(lengths) for i in range(1, len(sents))]   # share after sentence i (cut i)
    cuts, prev = [], 0
    for k, target in enumerate(targets, start=1):
        options = range(prev + 1, len(sents) - (n - k) + 1)                     # leave >= 1 sentence per later piece
        cut = min(options, key=lambda i: abs(shares[i - 1] - target))
        cuts.append(cut)
        prev = cut
    bounds = [0] + cuts + [len(sents)]
    return [" ".join(sents[a:b]) for a, b in zip(bounds, bounds[1:])], "proportional"


def bubble_lines(ocr_data: dict, bubble_id: str) -> list[dict]:
    """
    The bubble's text lines in reading order (top to bottom, then left to right): [{line_id, box, text}]. The text of
    a line comes from the page JSON (saved since 2026-11-02) or, for older pages, from the OCR words lying in the line
    box; "" when neither knows it (split_bubble then reads that piece again).
    """
    _find(ocr_data, bubble_id)
    items = ocr_data.get("items", [])
    out = []
    for l in ocr_data.get("lines", []):
        if l.get("bubble_id") != bubble_id:
            continue
        text = (l.get("text") or "").strip()
        if not text:
            x1, y1, x2, y2 = bbox_of(l["box"])
            words = [it for it in items if x1 - 2 <= center(bbox_of(it["box"]))[0] <= x2 + 2
                     and y1 - 2 <= center(bbox_of(it["box"]))[1] <= y2 + 2]
            text = " ".join(it["text"] for it in sorted(words, key=lambda it: bbox_of(it["box"])[0])).strip()
        out.append({"line_id": l["line_id"], "box": l["box"], "text": text})
    out.sort(key=lambda l: (round(bbox_of(l["box"])[1] / 6), bbox_of(l["box"])[0]))
    return out


def _split_plan(ocr_data: dict, bubble_id: str, new_line_ids, image=None, ocr_region_fn=None, protected=()) -> dict:
    """Pieces of the split (shared by the preview and the real split): kept lines first or in reading order of the page."""
    bubble = _find(ocr_data, bubble_id)
    lines = bubble_lines(ocr_data, bubble_id)
    if len(lines) < 2:
        raise BubbleEditError("This bubble has only one text line - nothing to split.", 400)
    chosen = set(new_line_ids or [])
    unknown = chosen - {l["line_id"] for l in lines}
    if unknown:
        raise BubbleEditError("Some of the chosen lines do not belong to this bubble.", 400)
    groups = [[l for l in lines if l["line_id"] not in chosen], [l for l in lines if l["line_id"] in chosen]]
    if not groups[0] or not groups[1]:
        raise BubbleEditError("Pick some, but not all, lines of the bubble for the new bubble.", 400)
    groups.sort(key=lambda g: min((round(bbox_of(l["box"])[1] / 6), bbox_of(l["box"])[0]) for l in g))   # source order

    pieces = []
    for g in groups:
        union = bbox_of(g[0]["box"])
        for l in g[1:]:
            union = union_bbox(union, bbox_of(l["box"]))
        text = " ".join(l["text"] for l in g if l["text"]).strip()
        reread = any(not l["text"] for l in g)
        if reread:
            if image is None or ocr_region_fn is None:
                text = ""                                     # preview: the real split reads it
            else:
                ih, iw = image.shape[:2]
                reading = read_region(image, expand(union, 0.15, iw, ih, min_pad=4), ocr_region_fn)
                text = _clean(reading["text"], protected) if reading else text
        pieces.append({"line_ids": [l["line_id"] for l in g], "bbox": union, "text": text, "reread": reread})

    sources = [p["text"] or " ".join(l["text"] for l in g) for p, g in zip(pieces, groups)]
    translations, method = split_translation(bubble.get("translation", ""), sources)
    ai_translations, _ = split_translation(bubble.get("ai_translation", ""), sources)
    for p, t, a in zip(pieces, translations, ai_translations):
        p["translation"], p["ai_translation"] = t, a
    edited_source = bool(bubble.get("text_raw")) and bubble.get("text_raw") != bubble.get("text")
    return {"pieces": pieces, "method": method, "edited_source": edited_source}


def split_preview(ocr_data: dict, bubble_id: str, new_line_ids) -> dict:
    """What split_bubble would produce (texts, divided translation, method) - nothing is changed, no OCR runs."""
    plan = _split_plan(ocr_data, bubble_id, new_line_ids)
    return {"method": plan["method"], "edited_source": plan["edited_source"],
            "pieces": [{k: p[k] for k in ("line_ids", "text", "translation", "reread")} for p in plan["pieces"]]}


def split_bubble(ocr_data: dict, bubble_id: str, new_line_ids, image=None, ocr_region_fn=None, protected=()) -> dict:
    """
    Split a bubble the OCR glued together (two speech bubbles, map labels) into two: `new_line_ids` form the new one.
    No OCR for lines whose text is known, and NO translation request: the existing translation is divided between
    the pieces (split_translation); a piece the division could not handle cleanly gets split_check=True (editor tag
    "check translation"). The first piece in reading order keeps the bubble id and list position. Review stamps are
    dropped (new text). Lines keep their boxes, so the clean image does not change.
    """
    bubble = _find(ocr_data, bubble_id)
    plan = _split_plan(ocr_data, bubble_id, new_line_ids, image, ocr_region_fn, protected)
    index = next(i for i, b in enumerate(ocr_data["bubbles"]) if b is bubble)
    new_id = f"bubble_{_next_number([b['bubble_id'] for b in ocr_data['bubbles']], 'bubble_')}"
    check = plan["method"] != "sentences" and bool((bubble.get("translation") or "").strip())
    made = []
    for k, piece in enumerate(plan["pieces"]):
        b = {key: v for key, v in bubble.items()
             if key not in ("confirmed_translation", "confirmed_at", "text_raw", "merged_by_user", "missed_text", "interior_box", "layout")}
        b.update(bubble_id=bubble_id if k == 0 else new_id, text=piece["text"], translation=piece["translation"],
                 ai_translation=piece["ai_translation"], box=rect_poly(piece["bbox"]), line_count=len(piece["line_ids"]),
                 split_from=bubble_id)
        if check:
            b["split_check"] = True
        if piece["text"]:
            reason = skip_reason(piece["text"], skip_symbols=get_setting("ocr_skip_symbols", True))
            if reason and not (reason == "number" and not get_setting("ocr_skip_numeric", True)):
                b["skip"], b["skip_reason"] = True, reason
            elif b.get("skip_reason") in ("number", "symbols"):
                b.pop("skip", None)
                b.pop("skip_reason", None)
        made.append(b)
        for line in ocr_data.get("lines", []):
            if line["line_id"] in piece["line_ids"]:
                line["bubble_id"] = b["bubble_id"]
                if piece["text"] and not line.get("text") and len(piece["line_ids"]) == 1:
                    line["text"] = piece["text"]
    ocr_data["bubbles"][index:index + 1] = made
    if image is not None:
        for b in made:
            _refresh_info(image, ocr_data, b)
    return {"bubbles": made, "method": plan["method"], "kept": bubble_id, "new": new_id}


def _check_move(ocr_data: dict, bubble_id: str, target_id: str) -> None:
    if target_id == bubble_id:
        raise BubbleEditError("Pick another bubble to move the lines to.", 400)
    target = _find(ocr_data, target_id)
    if target.get("skip"):
        raise BubbleEditError("That bubble is skipped - press 'Translate this' on it first.", 400)


def _moved_piece(ocr_data: dict, split_result: dict, line_ids) -> str:
    owner = {l["line_id"]: l.get("bubble_id") for l in ocr_data.get("lines", [])}
    return owner[list(line_ids)[0]]


def move_lines_preview(ocr_data: dict, bubble_id: str, line_ids, target_id: str) -> dict:
    """What move_lines would do - on a copy, no OCR: the divided translation and the target's joined text."""
    import copy
    _check_move(ocr_data, bubble_id, target_id)
    plan = split_preview(ocr_data, bubble_id, line_ids)
    data = copy.deepcopy(ocr_data)
    result = split_bubble(data, bubble_id, line_ids)
    joined = merge_preview(data, target_id, [_moved_piece(data, result, line_ids)])
    return {**plan, "target": {"bubble_id": target_id, "text": joined.get("text", ""), "translation": joined.get("translation", "")}}


def move_lines(ocr_data: dict, bubble_id: str, line_ids, target_id: str, image=None, ocr_region_fn=None, protected=()) -> dict:
    """
    Move some text lines of a bubble - with their part of the translation - into ANOTHER existing bubble (the OCR put
    a line into the wrong bubble). No translation request: it is split_bubble (the translation divided between the
    lines that stay and the ones that move) followed by merge_bubbles into the target (joined in reading order).
    Both bubbles lose their review stamps (new text); a division that was not clean tags them "check translation".
    """
    _check_move(ocr_data, bubble_id, target_id)
    result = split_bubble(ocr_data, bubble_id, line_ids, image, ocr_region_fn, protected)
    moved = _moved_piece(ocr_data, result, line_ids)
    stays = next(b["bubble_id"] for b in result["bubbles"] if b["bubble_id"] != moved)
    split_check = any(b.get("split_check") for b in result["bubbles"])
    merged = merge_bubbles(ocr_data, target_id, [moved], image)
    target = _find(ocr_data, target_id)
    if split_check:
        target["split_check"] = True
    return {"method": result["method"], "source": stays, "target": target_id, "merged": merged}


# ------------------------------------------------------------------ lettering layout

def bubble_text_rect(ocr_data: dict, bubble: dict) -> list | None:
    """
    [cx, cy, w, h, angle] of a bubble whose text is TILTED (map labels, diagonal captions), from the four-corner boxes
    of the OCR words inside the bubble box (kept in the page JSON "items"), else None. Typesetting letters such a bubble
    along that slant by default (utils/typesetting.plan_bubble).
    """
    from utils.ocr_rescue import tilted_text_rect
    # Pages OCR'd since tilted lines are marked: the bubble's own lines decide (a diagonal label's bounding box also
    # holds the centres of neighbouring level labels, so the word test below would see "mixed" text there).
    ids = set(bubble.get("line_ids", []))
    own = [l for l in ocr_data.get("lines", []) if l.get("bubble_id") == bubble.get("bubble_id") or l.get("line_id") in ids]
    if own and any("angle" in l for l in own):
        return tilted_text_rect([l["box"] for l in own]) if all("angle" in l for l in own) else None
    x1, y1, x2, y2 = bbox_of(bubble["box"])
    words = [it["box"] for it in ocr_data.get("items", [])
             if len(it.get("box", [])) == 4 and x1 - 3 <= center(bbox_of(it["box"]))[0] <= x2 + 3
             and y1 - 3 <= center(bbox_of(it["box"]))[1] <= y2 + 3]
    return tilted_text_rect(words)



LAYOUT_ALIGNS = ("center", "left", "right")
LAYOUT_VALIGNS = ("middle", "top", "bottom")
LAYOUT_STROKES = ("none", "thin", "thick")


def set_layout(ocr_data: dict, bubble_id: str, changes: dict) -> dict:
    """
    Store the user's Lettering choices of a bubble (page JSON bubble["layout"], read by utils/typesetting.plan_bubble):
      box        [x1, y1, x2, y2] text area in image coordinates; null = automatic (bubble interior)
      font_size  px; null = automatic (largest size that fits)
      angle      degrees clockwise, -180..180
      align      center | left | right
      stroke     none | thin | thick (white outline, for text over artwork)
      valign     middle | top | bottom
      font       font family name (services/font_service.py); null = the default font (Settings)
      bold / italic  true / false (imitated when the family has no such file)
      reset      true = forget everything (back to automatic)
    Only non-default values are kept, so an untouched bubble has no "layout" and renders exactly as before.
    """
    bubble = _find(ocr_data, bubble_id)
    layout = {} if changes.get("reset") else dict(bubble.get("layout") or {})
    if "box" in changes:
        box = changes["box"]
        if box is None:
            layout.pop("box", None)
        else:
            try:
                x1, y1, x2, y2 = (round(float(v), 1) for v in box)
            except (TypeError, ValueError):
                raise BubbleEditError("box must be [x1, y1, x2, y2]", 400)
            x1, x2 = sorted((x1, x2))
            y1, y2 = sorted((y1, y2))
            if x2 - x1 < 8 or y2 - y1 < 8:
                raise BubbleEditError("The text area is too small.", 400)
            layout["box"] = [x1, y1, x2, y2]
    if "font_size" in changes:
        if changes["font_size"] in (None, "", 0):
            layout.pop("font_size", None)
        else:
            try:
                layout["font_size"] = max(6, min(300, int(changes["font_size"])))
            except (TypeError, ValueError):
                raise BubbleEditError("font_size must be a number", 400)
    if "angle" in changes:
        try:
            angle = float(changes["angle"] or 0) % 360
        except (TypeError, ValueError):
            raise BubbleEditError("angle must be a number", 400)
        angle = round(angle - 360 if angle > 180 else angle, 1)
        if angle or bubble_text_rect(ocr_data, bubble):
            layout["angle"] = angle          # 0 is kept for tilted text: "level" overrides the automatic slant
        else:
            layout.pop("angle", None)
    if "font" in changes:
        name = changes["font"]
        if name in (None, ""):
            layout.pop("font", None)
        elif isinstance(name, str) and len(name) <= 200:
            layout["font"] = name.strip()
        else:
            raise BubbleEditError("font must be a font family name", 400)
    for key in ("bold", "italic"):
        if key in changes:
            if changes[key]:
                layout[key] = True
            else:
                layout.pop(key, None)
    for key, allowed, default in (("align", LAYOUT_ALIGNS, "center"), ("stroke", LAYOUT_STROKES, "none"),
                                  ("valign", LAYOUT_VALIGNS, "middle")):
        if key in changes:
            if changes[key] not in allowed:
                raise BubbleEditError(f"{key} must be one of {', '.join(allowed)}", 400)
            if changes[key] == default:
                layout.pop(key, None)
            else:
                layout[key] = changes[key]
    if layout:
        bubble["layout"] = layout
    else:
        bubble.pop("layout", None)
    return {"bubble_id": bubble_id, "layout": layout}


# ------------------------------------------------------------------ reading order

def _center_of(b: dict):
    return center(bbox_of(b["box"] if "box" in b else b["box_coords"]))


def auto_order(bubbles: list[dict], mode: str = "ttb") -> list[dict]:
    """
    Order bubbles for the translator / the LLM.
      ttb  top to bottom (then left to right) - what the OCR produces
      rtl  rows top to bottom, inside a row RIGHT to LEFT   (Japanese manga)
      ltr  rows top to bottom, inside a row left to right   (western comics, webtoon columns)
    A "row" is a group of bubbles whose centres are within ~0.8 of a bubble height of each other.
    LIMITATION: there is no panel detection. On a page whose panels are not in clean rows (a tall panel next to two
    stacked ones) the heuristic can be wrong - use "Move up / down" in the editor to fix single bubbles.
    """
    if mode not in ("ttb", "rtl", "ltr") or len(bubbles) < 2:
        return list(bubbles)
    if mode == "ttb":
        return sorted(bubbles, key=lambda b: (_center_of(b)[1], _center_of(b)[0]))

    heights = sorted(bbox_of(b.get("box") or b["box_coords"])[3] - bbox_of(b.get("box") or b["box_coords"])[1] for b in bubbles)
    tolerance = 0.8 * heights[len(heights) // 2]
    ordered = sorted(bubbles, key=lambda b: _center_of(b)[1])
    rows, current = [], [ordered[0]]
    for b in ordered[1:]:
        if abs(_center_of(b)[1] - sum(_center_of(x)[1] for x in current) / len(current)) <= tolerance:
            current.append(b)
        else:
            rows.append(current)
            current = [b]
    rows.append(current)
    out = []
    for row in rows:
        out.extend(sorted(row, key=lambda b: _center_of(b)[0], reverse=(mode == "rtl")))
    return out


def reorder_bubbles(ocr_data: dict, order: list[str] | None = None, auto: str | None = None) -> list[str]:
    """Reorder ocr_data['bubbles'] by an explicit id list, or automatically (see auto_order). Returns the new id order."""
    bubbles = ocr_data.get("bubbles", [])
    by_id = {b["bubble_id"]: b for b in bubbles}
    if auto:
        if auto not in ("ttb", "rtl", "ltr"):
            raise BubbleEditError("auto must be ttb, rtl or ltr", 400)
        new = auto_order(bubbles, auto)
    else:
        if not order or sorted(order) != sorted(by_id):
            raise BubbleEditError("order must list every bubble of the page exactly once", 400)
        new = [by_id[i] for i in order]
    ocr_data["bubbles"] = new
    if auto:
        ocr_data["bubble_order"] = auto          # the page's own reading order: shown by the editor, kept by Re-OCR
    return [b["bubble_id"] for b in new]
