from dotenv import load_dotenv

# test_ocr.py

load_dotenv()

import os
os.environ['OMP_NUM_THREADS'] = '3'
os.environ['FLAGS_allocator_strategy'] = 'naive_best_fit'
os.environ['FLAGS_fraction_of_gpu_memory_to_use'] = '0'
os.environ['KMP_DUPLICATE_LIB_OK'] = 'TRUE'

import logging
from sqlalchemy.orm import Session
from database import SessionLocal
from models import Page
from paddleocr import PaddleOCR

from ocr_utils.handle_duplicates import smart_deduplicate_by_lines
from ocr_utils.merge_boxes import *
from ocr_utils.draw import (
    draw_text_boxes, print_ocr_items_grouped, draw_slices,
    draw_marker_debug, draw_marker_relocation_debug,
    draw_merged_lines, print_merged_lines,
    print_detected_bubbles, draw_bubbles, make_marker_record
)
from ocr_utils.marker import *
from ocr_utils.text_utils import is_any_marker

logging.getLogger("ppocr").setLevel(logging.ERROR)

# ---------------- GLOBAL VALUES ----------------
WHITE_SPACE = True

TEXT_THRESHOLD = 0.85
SCALE = 1.0           # quality multiplier (increasing image ratio 1.5, making ocr and adjusting boxes to original image size
SLICE_H_RATIO = 0.1    # image_height * SLICE_H_RATIO = slice_h
OVERLAP_RATIO = 0.15   # SLICE_H * OVERLAP_RATIO = OVERLAP
IOU_THRESH = 0.5
# USE_SMART_MERGE = False  # box-merging - post-processing (not completed)
DEBUG = True             # ocr feedback in console & draws boxes on images
MARKER_DEBUG = True      # draw marker inject/detect positions for diagnostics
# MARKER_ENABLED = False

# --- GLOBAL VALUES: deduplication ---
DEDUP_WORD_THRESH    = 0.1   # min ułamek wspólnych słów żeby sprawdzać geometrię
DEDUP_GEO_THRESH     = 0.1   # min ułamek nakładania mniejszego boxa
DEDUP_MIN_OVERLAP_PX = 5    # ignoruj nakładanie < N px²




# ---------------- OCR INITIALIZATION ----------------
ocr = PaddleOCR(
    lang='en',
    use_textline_orientation=True,
    enable_mkldnn=True,
    cpu_threads=3,
    text_recognition_batch_size = 1,
    use_doc_unwarping=False,
    # text_det_unclip_ratio = 1.7,
    # text_rec_score_thresh = TEXT_THRESHOLD
    # det_limit_side_len=1000,
)

# ---------------- SCALE BACK BOXES ----------------
def to_original_coords(box):
    """Scale box coordinates back to original image size."""
    return [[x / SCALE, y / SCALE] for x, y in box]


# ---------------- OVERLAP H & RATIO ----------------
def get_overlap_size(image_height):
    """
    Compute slice height and overlap for OCR slicing.

    Uses discrete heuristics based on image size to ensure stable OCR
    behavior across small, medium, and large images.
    """

    # --- very small images ---
    if image_height < 1100 * SCALE:
    # if image_height < 600:
        return image_height, 0

    # --- small / medium ---
    elif image_height < 1600  * SCALE:
        slice_h = int(image_height * 0.6)

    # --- medium ---
    elif image_height < 3000 * SCALE:
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

# ---------------- packaging ocr results ----------------

def parse_ocr_results(result, slice_id, threshold):
    """Zamienia surowy wynik PaddleOCR na ustandaryzowaną listę słowników."""
    items = []
    if not result:
        return items

    for res in result:
        for t, s, b in zip(res["rec_texts"], res["rec_scores"], res["dt_polys"]):
            if s < threshold and not is_any_marker(t):
                continue
            items.append({
                "text": t,
                "score": s,
                "box": b,
                "slice_id": slice_id
            })
    return items


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
    marker_records = []

    while y < h:
        y_end = min(y + slice_h, h)

        slices.append((y, y_end))

        crop = image[y:y_end, :].copy()
        crop_original = crop.copy()  # Save original for potential re-shoot

        if WHITE_SPACE:
            white_original = crop.copy()
            pad_width = 100
            padded_crop = cv2.copyMakeBorder(white_original, 0, 0, 0, pad_width, cv2.BORDER_CONSTANT, value=[255, 255, 255])

        # inject marker (default position: bottom-right)
        # crop, _, marker_pos = inject_marker(crop, slice_id)
        if WHITE_SPACE:
            crop, _, marker_pos = inject_marker(padded_crop, slice_id)

        print(f"\n[SLICE {slice_id}] {y}:{y_end}")

        # 2. OCR
        result = ocr.predict(crop)
        slice_items = parse_ocr_results(result, slice_id, TEXT_THRESHOLD)

        # 3. Wstępna detekcja
        marker_item, found = find_marker(slice_items, slice_id, marker_pos)
        text_items_temp = remove_marker(slice_items, slice_id)
        needs_fix = (not found or marker_overlaps_text(marker_item, text_items_temp))

        # 4. FIX (jeśli trzeba) - to tylko aktualizuje dane
        if needs_fix:
            print(f"[SLICE {slice_id}] Fix needed...")
            marker_item, found, marker_pos, crop, slice_items = resolve_marker_state(
                ocr, crop_original, slice_items, slice_id, marker_pos, text_items_temp, TEXT_THRESHOLD
            )
            # Po naprawie musimy przeliczyć text_items dla dalszej logiki (np. rotacji)
            text_items_temp = remove_marker(slice_items, slice_id)

        # --- TERAZ JESTEŚMY W GŁÓWNYM POTOKU (wykonuje się zawsze!) ---

        # 5. Rotacja (jeśli found)
        angle = 0
        if found:
            angle = detect_rotation(marker_item, marker_pos, crop.shape)
            if angle != 0:
                print(f"[SLICE {slice_id}] {angle} DEG ROTATION DETECTED")
                slice_items = correct_boxes_by_angle(slice_items, angle, crop.shape)

        # 6. Logowanie i czyszczenie
        marker_records.append(make_marker_record(slice_id, y, marker_pos, marker_item, angle))

        # Ostateczne czyszczenie markerów
        slice_items = remove_marker(slice_items, slice_id)

        # 7. Przesunięcie do globala (zawsze)
        for item in slice_items:
            shifted_box = [[x, y0 + y] for x, y0 in item["box"]]
            item["box"] = shifted_box

        all_items.extend(slice_items)

        # 8. Krok pętli
        y += slice_h - overlap
        slice_id += 1

    return all_items, slices, marker_records

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


        # ------- print scalled image ----- --- temporally - debuging some errors ---
        # out_dir = os.path.join(project.workspace_path, "processed", chapter.number)
        # os.makedirs(out_dir, exist_ok=True)
        #
        # file_base_name = os.path.splitext(page.file_name)[0]
        # scaled_path = os.path.join(out_dir, f"test_ocr_{file_base_name}_scaled.jpeg")
        # cv2.imwrite(scaled_path, image_scaled)
        # ------- print scalled image ----- --- temporally - debuging some errors ---


        print(f"\n--- PAGE {page.order} ---")
        print("Running OCR...\n")

        # ---------------- OCR ----------------
        items, slices, marker_records = run_ocr_sliced(image_scaled)



        # ---------------- LINES MERGE ----------------
        grouped_lines = build_text_lines(items)

        # ---------------- DEDUPLICATION ----------------
        print("\n=== POST-PROCESSING: PAGE-LEVEL DEDUPLICATION ===")
        items = smart_deduplicate_by_lines(
            grouped_lines, items, image_scaled, ocr,
            word_thresh=DEDUP_WORD_THRESH,
            geo_thresh=DEDUP_GEO_THRESH,
            min_overlap_area=DEDUP_MIN_OVERLAP_PX
        )

        # ---------------- LINES MERGE ----------------
        grouped_lines = build_text_lines(items)

        # ---------------- BUBLES MERGE ----------------
        bubbles = group_lines_into_bubbles(grouped_lines)


        print(f"\nFINAL ITEMS: {len(items)}")

        # ---------------- DRAW ----------------
        if DEBUG:
            # 1. Rysujemy zielone grupy - grupy slow
            image_original = draw_merged_lines(image_original, grouped_lines, to_original_coords)

            # 2. Rysujemy czerwone grupy - pojedyncze slowa
            image_original = draw_text_boxes(items, image_original, to_original_coords)

            # 3. Rysujemy zielone grupy - zmergowane bloki tekstu w jeden dymek
            # image_original = draw_bubbles(image_original, bubbles, to_original_coords)


            print_detected_bubbles(bubbles) # print - konsola, draw - obrazek



            print_merged_lines(grouped_lines)

            print_ocr_items_grouped(items, to_original_coords)
            image_original = draw_slices(image_original, slices, SCALE)

            if MARKER_DEBUG:
                image_original = draw_marker_debug(image_original, marker_records, SCALE)
                image_original = draw_marker_relocation_debug(image_original, marker_records, SCALE)


        # ---------------- SAVE ----------------
        out_dir = os.path.join(project.workspace_path, "processed", chapter.number)
        os.makedirs(out_dir, exist_ok=True)

        file_base_name = os.path.splitext(page.file_name)[0]
        out_path = os.path.join(out_dir, f"test_ocr_{file_base_name}.png")

        cv2.imwrite(out_path, image_original)

        print("\n--- SUCCESS ---")
        print(out_path)

    finally:
        db.close()


if __name__ == "__main__":
    # test_ocr_from_db(page_id=13)

    for i in range(11, 19):
        test_ocr_from_db(page_id=i)