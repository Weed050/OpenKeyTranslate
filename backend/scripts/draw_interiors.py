
# backend/scripts/draw_interiors.py

"""
Debug picture for the bubble geometry: draws what the flood fill sees on a real page, so thresholds can be judged
by eye instead of guessed.

  green outline  = bubble interior (the white area inside the outline)
  red  outline   = interior of a bubble flagged "missed_text" (dark pixels no OCR box explains)
  blue boxes     = OCR text lines
  yellow         = the bubble box; a bubble WITHOUT an interior (text over artwork / open outline) gets only this

Usage:
    cd backend
    python scripts/draw_interiors.py --image "D:\\scans\\page_001.jpg" --json "<...>\\processed\\Chapter_1\\page_001_ocr.json" --out debug.png

The JSON is a processed page's *_ocr.json (needs the "lines" data saved by the current version; run Re-OCR on older pages).
"""

import argparse
import json
import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from services.inpaint_service import targets_from_json  # noqa: E402
from utils.image_io import load_image_bgr, write_image  # noqa: E402
from utils.inpainting import analyze_bubble  # noqa: E402


def draw(image: np.ndarray, ocr_data: dict) -> tuple[np.ndarray, dict]:
    targets = targets_from_json(ocr_data)
    if targets is None:
        raise SystemExit("This JSON has no line data - run Re-OCR on the page first.")
    _, bubbles = targets
    out = image.copy()
    stats = {"bubbles": len(bubbles), "with_interior": 0, "flagged": 0}
    for b in bubbles:
        for box in b["boxes"]:
            cv2.polylines(out, [np.array(box, np.int32).reshape(-1, 1, 2)], True, (255, 120, 0), 1)
        x1, y1, x2, y2 = (int(v) for v in b["bbox"])
        cv2.rectangle(out, (x1, y1), (x2, y2), (0, 220, 255), 1)
        it, info, _ = analyze_bubble(image, b)
        if it is None:
            continue
        stats["with_interior"] += 1
        flagged = info["missed_text"]
        stats["flagged"] += int(flagged)
        contours, _ = cv2.findContours(it.hull, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for c in contours:
            cv2.polylines(out, [c + np.array(it.origin, np.int32)], True, (0, 0, 255) if flagged else (0, 200, 0), 2)
        cv2.putText(out, f"{b['bubble_id']} {'MISSED?' if flagged else 'ok'} {info['extra_pixels']}px",
                    (int(it.bbox[0]), max(12, int(it.bbox[1]) - 4)), cv2.FONT_HERSHEY_SIMPLEX, 0.45,
                    (0, 0, 255) if flagged else (0, 140, 0), 1)
    return out, stats


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--image", required=True, help="the ORIGINAL page image")
    ap.add_argument("--json", required=True, help="the page's *_ocr.json")
    ap.add_argument("--out", default="bubble_debug.png")
    args = ap.parse_args()
    with open(args.json, "r", encoding="utf-8") as f:
        data = json.load(f)
    out, stats = draw(load_image_bgr(args.image), data)
    write_image(args.out, out)
    print(f"{stats['bubbles']} bubble(s), {stats['with_interior']} with a detected interior, {stats['flagged']} flagged as 'missed text'. Saved {args.out}")


if __name__ == "__main__":
    main()
