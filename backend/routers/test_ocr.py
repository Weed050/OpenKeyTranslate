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
from models import Page, Project, Chapter
from paddleocr import PaddleOCR

from routers.ocr_utils.handle_duplicates import smart_deduplicate_by_lines, remove_slice_boundary_duplicates
from routers.ocr_utils.merge_boxes import *
from routers.ocr_utils.draw import (
    draw_text_boxes, print_ocr_items_grouped, draw_slices,
    draw_marker_debug, draw_marker_relocation_debug,
    draw_merged_lines, print_merged_lines,
    print_detected_bubbles, draw_bubbles, make_marker_record
)
from routers.ocr_utils.marker import *
from routers.ocr_utils.text_utils import is_any_marker

logging.getLogger("ppocr").setLevel(logging.ERROR)

# ---------------- GLOBAL VALUES ----------------
WHITE_SPACE = True
WHITE_SPACE_PAD_WIDTH = 100

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
        crop_original = crop.copy()
        content_w = crop.shape[1]
        pad_width = WHITE_SPACE_PAD_WIDTH if WHITE_SPACE else 0

        if WHITE_SPACE:
            ocr_crop = pad_crop_right(crop_original, pad_width)
            ocr_crop, _, marker_pos = inject_marker(
                ocr_crop, slice_id, content_width=content_w
            )
        else:
            ocr_crop, _, marker_pos = inject_marker(crop_original.copy(), slice_id)

        print(f"\n[SLICE {slice_id}] {y}:{y_end}")

        # 2. OCR (always on ocr_crop — coords match slice_items)
        result = ocr.predict(ocr_crop)
        slice_items = parse_ocr_results(result, slice_id, TEXT_THRESHOLD)
        ocr_shape = ocr_crop.shape[:2]

        # 3. Wstępna detekcja
        # marker_item, found = find_marker(slice_items, slice_id, marker_pos)
        text_items_temp = remove_marker(slice_items, slice_id)

        # 4.1 Marker State Resolution (cała logika i logi wewnątrz funkcji)
        marker_item, found, marker_pos, ocr_crop, slice_items = resolve_marker_state(
            ocr,
            crop_original,
            ocr_crop,
            slice_items,
            slice_id,
            marker_pos,
            text_items_temp,
            TEXT_THRESHOLD,
            pad_width=pad_width,
        )

        # 4.2 Aktualizacja stanu po ewentualnych korektach resolvera
        ocr_shape = ocr_crop.shape[:2]
        text_items_temp = remove_marker(slice_items, slice_id)

        # 5. Rotacja — ocr_shape must match OCR box space (padded when WHITE_SPACE)
        angle = 0
        if found and len(text_items_temp) > 0:
            angle = detect_rotation(
                marker_item, marker_pos, ocr_shape, content_width=content_w if pad_width else None
            )
            if angle != 0:
                print(f"[SLICE {slice_id}] {angle} DEG ROTATION DETECTED")
                slice_items = correct_boxes_by_angle(slice_items, angle, ocr_shape)
        else:
            print(f"[SLICE {slice_id}] ROTATION correction skipped: no meaningfull text found in slice")

        # 6. Logowanie i czyszczenie
        marker_records.append(make_marker_record(slice_id, y, marker_pos, marker_item, angle))

        slice_items = remove_marker(slice_items, slice_id)

        # Clip x do szerokości treści (bez prawego paddingu markera)
        for item in slice_items:
            item["box"] = [
                [min(max(0.0, float(px)), float(content_w)), float(py)]
                for px, py in item["box"]
            ]

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

        image_width = image_scaled.shape[1]   # potrzebne do split_line_by_gap i boundary dedup
        _, overlap  = get_overlap_size(image_scaled.shape[0])

        # ---------------- PRE-DEDUP: granice sliców ----------------
        # Usuwa identyczne duplikaty z obszaru nakładania się sliców, których boxy Y-owo
        # nie nachodzą na siebie (same_line y_tol ≈ 24 px jest za ciasne na jitter ~72 px).
        # Musi być PRZED build_text_lines, żeby grupowanie linii dostało już czystą listę.
        items = remove_slice_boundary_duplicates(items, max_y_jitter=int(overlap * 1.5))

        # ---------------- LINES MERGE ----------------
        grouped_lines = build_text_lines(items, image_width=image_width)

        # ---------------- DEDUPLICATION ----------------
        print("\n=== POST-PROCESSING: PAGE-LEVEL DEDUPLICATION ===")
        items = smart_deduplicate_by_lines(
            grouped_lines, items, image_scaled, ocr,
            word_thresh=DEDUP_WORD_THRESH,
            geo_thresh=DEDUP_GEO_THRESH,
            min_overlap_area=DEDUP_MIN_OVERLAP_PX
        )

        # ---------------- LINES MERGE ----------------
        grouped_lines = build_text_lines(items, image_width=image_width)

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
            image_original = draw_bubbles(image_original, bubbles, to_original_coords)


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










































def run_batch_ocr(pages_per_chapter: int = 10):
    """
    Automatyczne przetwarzanie masowe:
    - Pobiera wszystkie projekty z bazy danych.
    - Z każdego projektu wybiera maksymalnie 3 pierwsze rozdziały.
    - Z każdego rozdziału przetwarza do `pages_per_chapter` stron.
    """
    db: Session = SessionLocal()
    try:
        projects = db.query(Project).all()
        if not projects:
            print("[BATCH] Brak projektów w bazie danych.")
            return

        print(f"\n🚀 Uruchamiam automatyczne przetwarzanie masowe...")
        print(f"   Ustawienie: max {pages_per_chapter} stron z max 3 rozdziałów na projekt.\n")

        for project in projects:
            print("═" * 70)
            print(f"📁 PROJEKT WORKSPACE: {project.workspace_path}")
            print("═" * 70)

            # Pobieramy do 3 rozdziałów dla projektu przy użyciu bezpiecznego getattr
            chapters = getattr(project, "chapters", [])[:3]

            if not chapters:
                print("   (Brak rozdziałów w tym projekcie)")
                continue

            for chapter in chapters:
                print(f"\n  └── 📖 Rozdział nr: {chapter.number}")

                # Pobieramy do N stron dla tego rozdziału
                pages = getattr(chapter, "pages", [])[:pages_per_chapter]
                if not pages:
                    print("      (Brak stron w tym rozdziale)")
                    continue

                print(f"      Znaleziono {len(pages)} stron do przetworzenia.")

                for page in pages:
                    print(f"      ├── 📄 Odpalam Page ID: {page.id} ({page.file_name})...")
                    try:
                        # Wywołujemy Twoją bazową funkcję testową
                        test_ocr_from_db(page_id=page.id)
                    except Exception as e:
                        # Zabezpieczenie batcha przed całkowitym wywaleniem w kosmos
                        print(f"      ❌ [BŁĄD] Pomijam stronę ID {page.id} z powodu błędu: {e}")
                        continue

        print("\n🎉 Masowe przetwarzanie wsadowe zostało zakończone!")
    finally:
        db.close()









def test_ocr_from_path(img_path: str, out_dir: str = "./test_output"):
    """Szybki test OCR bezpośrednio z pliku na dysku, omijający bazę danych."""
    import cv2
    import numpy as np

    print(f"\nIMG PATH: {img_path}")

    # --- Wczytywanie pliku z polskimi/specjalnymi znakami w ścieżce ---
    if not os.path.exists(img_path):
        print("Plik nie istnieje! Sprawdź ścieżkę.")
        return

    image = cv2.imdecode(np.fromfile(img_path, np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        print("Błąd dekodowania obrazu.")
        return

    # channel alpha
    if len(image.shape) == 3 and image.shape[2] == 4:
        image = cv2.cvtColor(image, cv2.COLOR_BGRA2BGR)

    # scaling
    image_original = image.copy()
    image_scaled = cv2.resize(image_original, None, fx=SCALE, fy=SCALE)

    print("Running OCR...\n")

    # ---------------- OCR ----------------
    items, slices, marker_records = run_ocr_sliced(image_scaled)

    image_width = image_scaled.shape[1]
    _, overlap = get_overlap_size(image_scaled.shape[0])

    items = remove_slice_boundary_duplicates(items, max_y_jitter=int(overlap * 1.5))

    grouped_lines = build_text_lines(items, image_width=image_width)

    # ---------------- DEDUPLICATION ----------------
    print("\n=== POST-PROCESSING: PAGE-LEVEL DEDUPLICATION ===")
    items = smart_deduplicate_by_lines(
        grouped_lines, items, image_scaled, ocr,
        word_thresh=DEDUP_WORD_THRESH,
        geo_thresh=DEDUP_GEO_THRESH,
        min_overlap_area=DEDUP_MIN_OVERLAP_PX
    )

    grouped_lines = build_text_lines(items, image_width=image_width)

    # ---------------- BUBLES MERGE ----------------
    bubbles = group_lines_into_bubbles(grouped_lines)

    print(f"\nFINAL ITEMS: {len(items)}")

    # ---------------- DRAW ----------------
    if DEBUG:
        image_original = draw_merged_lines(image_original, grouped_lines, to_original_coords)
        image_original = draw_text_boxes(items, image_original, to_original_coords)
        print_detected_bubbles(bubbles)
        print_merged_lines(grouped_lines)
        print_ocr_items_grouped(items, to_original_coords)
        image_original = draw_slices(image_original, slices, SCALE)
        image_original = draw_bubbles(image_original, bubbles, to_original_coords)

        if MARKER_DEBUG:
            image_original = draw_marker_debug(image_original, marker_records, SCALE)
            image_original = draw_marker_relocation_debug(image_original, marker_records, SCALE)

    # ---------------- SAVE ----------------
    os.makedirs(out_dir, exist_ok=True)

    file_base_name = os.path.splitext(os.path.basename(img_path))[0]
    out_path = os.path.join(out_dir, f"test_ocr_local_{file_base_name}.png")

    cv2.imwrite(out_path, image_original)

    print("\n--- SUCCESS ---")
    print(f"Zapisano wynik do: {out_path}")






















if __name__ == "__main__":
    # test_ocr_from_db(page_id=13)

    # for i in range(11, 19):
    #     test_ocr_from_db(page_id=i)

    # test_ocr_from_path(param1, param2)
    # test_ocr_from_path(param3, param4)



    ILOSC_STRON = 10
    print(f"Uruchamiam automatyczne przetwarzanie: po {ILOSC_STRON} strony z każdego rozdziału.")
    run_batch_ocr(pages_per_chapter=ILOSC_STRON)