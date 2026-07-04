
# backend/test_main.py

"""
Local standalone execution script for testing the OCR and Translation pipelines.

RATIONALE & ARCHITECTURE:
This script acts as a dedicated, lightweight entry point used exclusively for local
testing and rapid prototyping. It completely bypasses the main HTTP server layer (`main.py`),
FastAPI framework, and API routing. By decoupling the core algorithmic pipeline from the web
server infrastructure, developers can execute, debug, and fine-tune spatial layouts, OCR
thresholds, text merging rules, and translation logic instantaneously.

It draws image metadata straight from the database to mimic real application states, processes
the images through the full pipeline, and dumps rich visual debug outputs directly to disk.
"""

import json
import os
import cv2
import numpy as np
from dotenv import load_dotenv

# Initialize environment variables before pulling down configurations
load_dotenv()

from sqlalchemy.orm import Session
from core.database import SessionLocal
from models.models import Page, Project

from core.config import (
    SCALE, DEBUG, MARKER_DEBUG, TRANSLATION_ON, GROQ_MODEL
)
from services.ocr_pipeline import process_image, to_original_coords
from utils.inpainting import erase_text_from_image
from services.translation_service import translate_bubbles

from utils.draw_debug import (
    draw_text_boxes, draw_slices, draw_marker_debug,
    draw_marker_relocation_debug, draw_merged_lines, draw_bubbles,
)
from utils.console_report import (
    print_ocr_items_grouped, print_merged_lines,
    print_detected_bubbles, print_translations_to_console,
)
from utils.typesetting import draw_translated_text_on_clean_image


def test_ocr_from_db(page_id: int):
    """
    Executes a comprehensive, end-to-end integration test for a specific page ID.

    Loads the original source image, feeds it into the isolated OCR/Translation
    pipeline, handles optional visual debug layers, and flushes output files to disk.
    """
    db: Session = SessionLocal()
    try:
        # Fetch target page context from the local database
        page = db.query(Page).filter(Page.id == page_id).first()
        if not page:
            print("Page with ID {page_id} not found in database.")
            return

        chapter = page.chapter
        project = chapter.project

        # Reconstruct the absolute path to the raw image file based on project structure
        img_path = os.path.join(project.workspace_path, "raw", chapter.number, page.file_name)
        print(f"\nIMG: {img_path}")

        # Decode image using numpy to safely handle Unicode/special characters in file paths
        image = cv2.imdecode(np.fromfile(img_path, np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            print("Image decode error")
            return

        # Strip the alpha channel if the image is in BGRA format
        if len(image.shape) == 3 and image.shape[2] == 4:
            image = cv2.cvtColor(image, cv2.COLOR_BGRA2BGR)

        # Keep an untouched copy of the original image for printing translation results and debuging
        image_original = image.copy()
        print(f"\n--- PAGE {page.order} ---")

        # ==========================================
        # STEP 1: Execute Core OCR Pipeline (The Black Box)
        # ==========================================
        ocr_result = process_image(image)

        # Translation Execution (Conditional)
        if TRANSLATION_ON:
            print(f"[AI] Tłumaczenie przy użyciu modelu: {GROQ_MODEL}...")
            ocr_result["bubbles"] = translate_bubbles(ocr_result["bubbles"])
            print_translations_to_console(ocr_result["bubbles"])

        items = ocr_result["items"]
        grouped_lines = ocr_result["lines"]
        bubbles = ocr_result["bubbles"]
        debug_data = ocr_result["debug"]

        # Prepare output directory in the processed workspace
        out_dir = os.path.join(project.workspace_path, "processed", chapter.number)
        os.makedirs(out_dir, exist_ok=True)
        file_base_name = os.path.splitext(page.file_name)[0]

        # Export pipeline data to JSON for frontend usage or manual inspection
        save_ocr_json(out_dir, file_base_name, ocr_result)

        # ---------------- Inpainting (Text Removal) ----------------
        print("\nRunning Inpainting (Text Erasing) from Main Pipeline...")
        inpainted_image = erase_text_from_image(image, ocr_result["lines"], to_original_coords)

        # ---------------- Printing translation (Typesetting (Text Rendering)) ------
        print("Running Typesetting (Drawing new text)...")
        print(f"[DEBUG SHAPE] image_original: {image_original.shape}, inpainted: {inpainted_image.shape}")
        translated_image = draw_translated_text_on_clean_image(inpainted_image, image_original, bubbles, to_original_coords)

        # ---------------- DRAW & SAVE (Debug Visualizations & File Outputs) ----------------
        if DEBUG:
            print(f"\nFINAL ITEMS: {len(items)}")

            # Overlay bounding boxes, grouping lines, and bubble geometries onto the original image
            image_original = draw_merged_lines(image_original, grouped_lines, to_original_coords)
            image_original = draw_text_boxes(items, image_original, to_original_coords)
            image_original = draw_bubbles(image_original, bubbles, to_original_coords)

            print_detected_bubbles(bubbles)
            print_merged_lines(grouped_lines)
            print_ocr_items_grouped(items, to_original_coords)

            # Draw slicing boundaries used during the image division phase
            image_original = draw_slices(image_original, debug_data["slices"], SCALE)

            if MARKER_DEBUG:
                # Highlight synchronization markers if marker debugging is explicitly enabled
                image_original = draw_marker_debug(image_original, debug_data["marker_records"], SCALE)
                image_original = draw_marker_relocation_debug(image_original, debug_data["marker_records"], SCALE)

        out_dir = os.path.join(project.workspace_path, "processed", chapter.number)
        os.makedirs(out_dir, exist_ok=True)
        file_base_name = os.path.splitext(page.file_name)[0]

        out_path_debug = os.path.join(out_dir, f"ocr_{file_base_name}_debug.png")
        out_path_clean = os.path.join(out_dir, f"ocr_{file_base_name}_inpainted.png")
        out_path_final = os.path.join(out_dir, f"ocr_{file_base_name}_translated.png")

        # ---------------- saving inpainted image (flush all generated visual maps to disk) ----------------
        cv2.imwrite(out_path_debug, image_original)
        cv2.imwrite(out_path_clean, inpainted_image)
        cv2.imwrite(out_path_final, translated_image)

        print("\n--- SUCCESS ---\n")
        print(f"Debug:       {out_path_debug}")
        print(f"Inpainted:   {out_path_clean}")
        print(f"Translated:  {out_path_final}\n")

    finally:
        db.close()


def save_ocr_json(out_dir: str, file_base_name: str, ocr_result: dict):
    """
    Save OCR results (items, lines, bubbles with translations) to JSON.
    Useful for debugging and as input for the editor later.
    """
    payload = {
        "items": [
            {
                "text":     item["text"],
                "score":    round(item["score"], 4),
                "slice_id": item.get("slice_id"),
                "box":      [[round(x, 1), round(y, 1)] for x, y in item["box"]],
            }
            for item in ocr_result["items"]
        ],
        "bubbles": [
            {
                "bubble_id":   b["bubble_id"],
                "text":        b["text"],
                "translation": b.get("translation", ""),
                "box":         b["box_coords"],
                "line_count":  b["line_count"],
                "avg_score":   round(b["avg_score"], 4),
            }
            for b in ocr_result["bubbles"]
        ],
    }

    out_path = os.path.join(out_dir, f"{file_base_name}_ocr.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print(f"[JSON] saved -> {out_path}")

















def run_batch_ocr(pages_per_chapter: int = 10):
    """
    Execute automated batch OCR processing across all active projects and chapters.

    Iterates through database-backed projects, limits the scope to a controlled
    number of chapters and pages per chapter, and triggers the test pipeline
    independently for each page with robust exception handling.
    """
    db: Session = SessionLocal()
    try:
        # Fetch all available projects from the database
        projects = db.query(Project).all()
        if not projects:
            print("[BATCH] No projects found in the database.")
            return

        print("\n[START] Starting automated batch processing...")
        for project in projects:
            print("═" * 70)
            print(f"[WORKSPACE] PROJECT WORKSPACE: {project.workspace_path}")

            # Process a limited preview subset of chapters per project
            chapters = getattr(project, "chapters", [])[:3]
            for chapter in chapters:
                print(f"\n  └── [CHAPTER] Chapter No: {chapter.number}")

                # Process up to the specified maximum number of pages per chapter
                pages = getattr(chapter, "pages", [])[:pages_per_chapter]
                for page in pages:
                    print(f"      ├── [PAGE] Processing Page ID: {page.id} ({page.file_name})...")
                    try:
                        # Core pipeline call using database records
                        test_ocr_from_db(page_id=page.id)
                    except Exception as e:
                        # Gracefully catch pipeline errors to ensure subsequent files continue processing
                        print(f"      [X]   [ERROR] Skipping page ID {page.id} due to failure: {e}")
                        continue

        print("\n[SUCCESS] Automated batch processing completed successfully!")
    finally:
        db.close()













def test_ocr_from_path(img_path: str, out_dir: str = "./test_output"):
    print(f"\nIMG PATH: {img_path}")
    if not os.path.exists(img_path):
        print("File does not exist! Check the path.")
        return

    file_base_name = os.path.splitext(os.path.basename(img_path))[0]
    os.makedirs(out_dir, exist_ok=True)

    # Load image supporting unicode paths
    image = cv2.imdecode(np.fromfile(img_path, np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        return

    # Convert BGRA to BGR if alpha channel is present
    if len(image.shape) == 3 and image.shape[2] == 4:
        image = cv2.cvtColor(image, cv2.COLOR_BGRA2BGR)

    image_original = image.copy()

    # ----------------- OCR PIPELINE RUN -----------------
    ocr_result = process_image(image)

    # ----------------- TRANSLATION -----------------
    if TRANSLATION_ON:
        print(f"[AI] Translating using model: {GROQ_MODEL}...")
        ocr_result["bubbles"] = translate_bubbles(ocr_result["bubbles"])
        print_translations_to_console(ocr_result["bubbles"])

    # Save translation results to JSON
    save_ocr_json(out_dir, file_base_name, ocr_result)

    # ----------------- INPAINTING -----------------
    print("\nRunning Inpainting (Text Erasing) from Main Pipeline...")
    inpainted_image = erase_text_from_image(image, ocr_result["lines"], to_original_coords)

    # ----------------- TYPESETTING (CONDITIONAL) -----------------
    translated_image = None
    if TRANSLATION_ON:
        print("Running Typesetting (Drawing new text)...")
        print(f"[DEBUG SHAPE] image_original: {image_original.shape}, inpainted: {inpainted_image.shape}")
        translated_image = draw_translated_text_on_clean_image(
            inpainted_image, image_original, ocr_result["bubbles"], to_original_coords
        )

    # ----------------- DRAW & LOGGING -----------------
    if DEBUG:
        # Render visual overlays onto the original image
        image_original = draw_merged_lines(image_original, ocr_result["lines"], to_original_coords)
        image_original = draw_text_boxes(ocr_result["items"], image_original, to_original_coords)
        image_original = draw_bubbles(image_original, ocr_result["bubbles"], to_original_coords)
        image_original = draw_slices(image_original, ocr_result["debug"]["slices"], SCALE)

        # Print detailed grouping and confidence data to console
        print(f"\nFINAL ITEMS (after deduplication): {len(ocr_result['items'])}")
        print_ocr_items_grouped(ocr_result["items"], to_original_coords)
        print_merged_lines(ocr_result["lines"])
        print_detected_bubbles(ocr_result["bubbles"])

    if MARKER_DEBUG:
        image_original = draw_marker_debug(image_original, ocr_result["debug"]["marker_records"], SCALE)
        image_original = draw_marker_relocation_debug(image_original, ocr_result["debug"]["marker_records"], SCALE)

    # ----------------- SAVE OUTPUTS -----------------
    out_path_debug = os.path.join(out_dir, f"ocr_{file_base_name}_debug.png")
    out_path_clean = os.path.join(out_dir, f"ocr_{file_base_name}_inpainted.png")

    cv2.imwrite(out_path_debug, image_original)
    cv2.imwrite(out_path_clean, inpainted_image)

    print("\n--- SUCCESS ---")
    print(f"Debug:       {out_path_debug}")
    print(f"Inpainted:   {out_path_clean}")

    # Save translated image only if Typesetting was executed
    if TRANSLATION_ON and translated_image is not None:
        out_path_final = os.path.join(out_dir, f"ocr_{file_base_name}_translated.png")
        cv2.imwrite(out_path_final, translated_image)
        print(f"Translated:  {out_path_final}")


if __name__ == "__main__":

    # _______________ EXECUTION MODE 1: SINGLE PAGE FROM DB _______________

    test_ocr_from_db(page_id=14)

    # _______________ EXECUTION MODE 2: BATCH PROCESSING (without interruption)  _______________

    # AMOUNT_OF_PAGES = 10
    # print(f"Running automatic processing: {AMOUNT_OF_PAGES} pages from each chapter.")
    # run_batch_ocr(pages_per_chapter=AMOUNT_OF_PAGES)

    # _______________ EXECUTION MODE 3: DIRECT FILE PATH  _______________

    # param1 = r""
    # param2 = r""
    # test_ocr_from_path(param1, param2)
