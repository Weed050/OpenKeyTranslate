
from dotenv import load_dotenv

load_dotenv()

import os
os.environ['OMP_NUM_THREADS'] = '3'
os.environ['FLAGS_allocator_strategy'] = 'naive_best_fit'
os.environ['FLAGS_fraction_of_gpu_memory_to_use'] = '0'
os.environ['KMP_DUPLICATE_LIB_OK'] = 'TRUE'

import numpy as np
import logging
from sqlalchemy.orm import Session
from database import SessionLocal
from models import Page
from paddleocr import PaddleOCR

# from ocr_utils.ocr_merge_utils import smart_merge, merge_lines
from ocr_utils.draw import draw_text_boxes, print_ocr_items_grouped, draw_slices
from ocr_utils.marker_ocr_utilities import *

logging.getLogger("ppocr").setLevel(logging.ERROR)


# ---------------- GLOBAL VALUES ----------------

TEXT_THRESHOLD = 0.85
SCALE = 1.5            # quality multiplier (increasing image ratio 1.5, making ocr and adjusting boxes to original image size
SLICE_H_RATIO = 0.1    # image_height * SLICE_H_RATIO = slice_h
OVERLAP_RATIO = 0.15   # SLICE_H * OVERLAP_RATIO = OVERLAP
IOU_THRESH = 0.5
USE_SMART_MERGE = False  # box-merging - post-processing (not completed)
DEBUG = True             # ocr feedback in console & draws boxes on images


# ---------------- OCR INITIALIZATION ----------------
ocr = PaddleOCR(
    lang='en',
    use_textline_orientation=True,
    enable_mkldnn=True,
    cpu_threads=3,
    rec_batch_num=1,
    use_doc_unwarping=False,
)

# ---------------- SCALE BACK BOXES ----------------
def to_original_coords(box):
    """Scale box coordinates back to original image size."""
    return [[x / SCALE, y / SCALE] for x, y in box]
#
# # ---------------- FLIP DETECTION AND CORRECTION ----------------
#
# def inject_marker(crop, slice_id):
#     """
#
#     :param crop:
#     :param slice_id:
#     :return:
#     """
#     marker_text = f"__M{slice_id}__"
#
#     h, w = crop.shape[:2]
#
#     margin = 20
#     pos = (w - 120, h - 20)  # prawy dół
#
#     cv2.putText(
#         crop,
#         marker_text,
#         pos,
#         cv2.FONT_HERSHEY_SIMPLEX,
#         0.7,
#         (0, 0, 0),
#         2
#     )
#
#     return crop, marker_text, pos
#
# def find_marker(items, marker_text):
#     for item in items:
#         txt = item["text"]
#
#         if marker_text in txt:
#             return item
#
#     return None
#
#
# def detect_flip(marker_item, slice_shape):
#     h, w = slice_shape[:2]
#
#     box = marker_item["box"]
#
#     # środek boxa
#     xs = [p[0] for p in box]
#     ys = [p[1] for p in box]
#
#     cx = sum(xs) / 4
#     cy = sum(ys) / 4
#
#     # jeśli marker jest w dolnej prawej ćwiartce → OK
#     if cx > w * 0.55 and cy > h * 0.55:
#         return False
#
#     if cx < w * 0.45 and cy < h * 0.45:
#         return True
#
#     # niepewne → traktuj jako brak flipa
#     return False
#
# def correct_boxes_180(items, slice_shape):
#     h, w = slice_shape[:2]
#
#     for item in items:
#         new_box = []
#         for x, y in item["box"]:
#             new_box.append([w - x, h - y])
#
#         item["box"] = order_box(new_box)
#
#     return items
#
# def remove_marker(items, marker_text):
#     return [item for item in items if marker_text not in item["text"]]
#
# def order_box(box):
#     box = sorted(box, key=lambda p: (p[1], p[0]))
#     top = sorted(box[:2], key=lambda p: p[0])
#     bottom = sorted(box[2:], key=lambda p: p[0])
#     return [top[0], top[1], bottom[1], bottom[0]]

# ---------------- OVERLAP H & RATIO ----------------
def get_overlap_size(image_height):
    """
    Compute slice height and overlap for OCR slicing.

    Uses discrete heuristics based on image size to ensure stable OCR
    behavior across small, medium, and large images.
    """

    # --- very small images ---
    if image_height < 600:
        return image_height, 0

    # --- small / medium ---
    elif image_height < 1200:
        slice_h = int(image_height * 0.6)

    # --- medium ---
    elif image_height < 3000:
        slice_h = int(image_height * 0.4)

    # --- large ---
    else:
        slice_h = int(image_height * 0.15)

    # clamp slice
    slice_h = max(250, min(slice_h, 1400))

    # overlap = percentage but bounded
    overlap = int(slice_h * 0.15)
    overlap = min(max(overlap, 40), 160)

    return slice_h, overlap

# ---------------- SLICING, OCR ----------------
def run_ocr_sliced(image):
    """
        Run OCR on image using vertical slicing.

        Splits image into overlapping horizontal slices to improve OCR accuracy
        on large images. Adjusts detected boxes back to global coordinates.
        :param image: scaled image
        :return: list of OCR items (text, score, box)
        """

    h, w = image.shape[:2]

    slice_h, overlap = get_overlap_size(h)

    all_items = []

    y = 0
    slice_id = 0
    slices = []

    while y < h:
        y_end = min(y + slice_h, h)

        slices.append((y, y_end))

        crop = image[y:y_end, :].copy()

        # inject marker
        crop, _, marker_pos = inject_marker(crop, slice_id)

        print(f"\n[SLICE {slice_id}] {y}:{y_end}")

        result = ocr.predict(crop)

        slice_items = []

        if result:
            for res in result:
                texts = res["rec_texts"]
                scores = res["rec_scores"]
                boxes = res["dt_polys"]

                for t, s, b in zip(texts, scores, boxes):

                    # if s < TEXT_THRESHOLD and "__M" not in t: # pomijanie w odrzucaniu markerow wykrywania obrotu
                    #     continue

                    # lokalne boxy
                    slice_items.append({
                        "text": t,
                        "score": s,
                        "box": b,
                        "slice_id": slice_id
                    })

        # marker logic
        marker_item, found = find_marker(slice_items, slice_id, marker_pos)

        if found:
            if slice_has_meaningful_text(slice_items, slice_id, TEXT_THRESHOLD):
                if detect_flip(marker_item, marker_pos, crop.shape):
                    print(f"[SLICE {slice_id}] FLIPPED detected, (correcting ...)")
                    slice_items = correct_boxes_180(list(slice_items), crop.shape)
            else:
                print(f"[SLICE {slice_id}] (skipping flip) check — no meaningful text in slice")

        # usun marker
        slice_items = remove_marker(slice_items, slice_id)

        # shift do globalnych coords
        for item in slice_items:
            shifted_box = [[x, y0 + y] for x, y0 in item["box"]]
            item["box"] = shifted_box

        # dodaj do globalnych
        all_items.extend(slice_items)

        y += slice_h - overlap
        slice_id += 1

    return all_items, slices

# ---------------- MAIN ----------------
def test_ocr_from_db(page_id: int):
    db: Session = SessionLocal()

    try:
        # --- DB ---
        page = db.query(Page).filter(Page.id == page_id).first()
        if not page:
            print("Page not found")
            return

        chapter = page.chapter
        project = chapter.project

        img_path = os.path.join(
            project.workspace_path,
            "raw",
            chapter.number,
            page.file_name
        )

        print(f"\nIMG: {img_path}")

        # --- "preprocessing" ---

        image = cv2.imdecode(np.fromfile(img_path, np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            print("Image decode error")
            return

        # channel alpha
        if len(image.shape) == 3 and image.shape[2] == 4:
            image = cv2.cvtColor(image, cv2.COLOR_BGRA2BGR)

        # scaling
        image_original = image.copy()
        image_scaled = cv2.resize(image_original, None, fx=SCALE, fy=SCALE)

        print(f"\n--- PAGE {page.order} ---")
        print("Running OCR...\n")

        # ---------------- OCR ----------------
        items, slices = run_ocr_sliced(image_scaled)

        # ---------------- GLOBAL DEDUPLICATE (FALSE RN) ----------------
        # if USE_SMART_MERGE:
        #     items = smart_merge(items, IOU_THRESH)
        #     items = merge_lines(items)

        print(f"\nFINAL ITEMS: {len(items)}")

        # ---------------- DRAW ----------------
        image_original = draw_text_boxes(items, image_original, to_original_coords)

        if DEBUG:
            print_ocr_items_grouped(items, to_original_coords)
            image_original = draw_slices(image_original, slices, SCALE)

        # ---------------- SAVE ----------------
        out_dir = os.path.join(project.workspace_path, "processed", chapter.number)
        os.makedirs(out_dir, exist_ok=True)

        out_path = os.path.join(out_dir, f"test_ocr_{page.file_name}_.jpeg")

        cv2.imwrite(out_path, image_original)

        print("\n--- SUCCESS ---")
        print(out_path)

    finally:
        db.close()


if __name__ == "__main__":
    test_ocr_from_db(page_id=3)