

# backend/utils/console_report.py

"""
Console Reporting and Diagnostics Utility Module
________________________________________________

This module provides functions for formatting and printing utilities
designed to render structured, human-readable diagnostic reports directly to
the developer console. It serves as the primary visualization layer during
the text extraction, structural grouping, and translation lifecycles.

Key Architectural Features:
    1. Multi-Stage Pipeline Tracking: Contains dedicated reporting functions
       tailored for distinct operational phases: raw sliced OCR outputs, merged
       text lines, consolidated speech bubbles, and final LLM translations.
    2. Spatial Sorting & Data Re-mapping: Sorts local text pieces vertically
       within their respective horizontal slices and translates pixel boundaries
       back to native image coordinates for meaningful structural verification.
    3. Comparative Evaluation: Provides side-by-side/itemized breakdowns
       matching original English texts with their localized Polish targets to
       facilitate immediate runtime code review and quality assessment.
"""

def print_ocr_items_grouped(items, to_original_coords):
    """
    Print raw OCR text items to the console, grouped by their respective slice IDs.

    This function sorts the extracted text items vertically (by their Y-coordinate)
    within each slice and formats the output to display the detected text,
    confidence score, and the top-left anchor coordinate scaled back to the original image size.

    Args:
        items (list[dict]): A list of OCR item dictionaries containing 'text', 'score',
                            'box', and 'slice_id'.

        to_original_coords (callable): A function that maps localized scaled bounding box
                                       coordinates back to the original image coordinate space.
    """

    print("\n--- OCR RESULTS ---")

    slices_map = {}
    for item in items:
        sid = item.get("slice_id", -1)
        slices_map.setdefault(sid, []).append(item)

    for sid in sorted(slices_map.keys()):
        print(f"\n[S{sid}]")

        slice_items = slices_map[sid]

        # Sort items vertically within the slice using the top-left Y coordinate
        slice_items.sort(key=lambda x: to_original_coords(x["box"])[0][1])

        for item in slice_items:
            box = to_original_coords(item["box"])
            x, y = box[0]

            print(f"   {item['text']:<15} | score={item['score']:.2f} ({int(x)}, {int(y)})")




def print_merged_lines(grouped_lines):
    """
        Print merged text lines to the console in a structured table.

        After individual OCR words are grouped into logical horizontal/vertical lines,
        this function provides a clean overview (debug on console) of the reconstructed lines,
        displaying their ID, full text content, confidence score, and word count.

        Args:
            grouped_lines (list[dict]): A list of text line dictionaries containing 'line_id',
                                        'text', 'score', and 'word_ids'.
        """
    print("\n" + "=" * 50)
    print(f"{'LINE ID':<10} | {'TEXT CONTENT':<40} | {'SCORE':<5}")
    print("-" * 50)

    for line in grouped_lines:
        line_id = line.get("line_id", "N/A")
        text = line.get("text", "")
        score = line.get("score", 0.0)

        # Opcjonalnie: możemy też pokazać ile słów składa się na linię
        word_count = len(line.get("word_ids", []))

        print(f"{line_id:<10} | {text:<40} | {score:.2f} ({word_count} words)")

    print("=" * 50 + "\n")




def print_detected_bubbles(bubbles):
    """
    Print extracted text boxes within the grouped bubble.

    This function dynamically calculates column widths based on the longest text string
    to ensure the table renders perfectly without breaking the layout (in console).

    Args:
        bubbles (list[dict]): A list of grouped bubble dictionaries containing
                              'bubble_id' and 'text'.
    """
    if not bubbles:
        print("\n[INFO] No text bubbles detected to display.")
        return

    col1_w = 12

    # Ensure the text column is at least 45 characters wide, or matches the longest text
    col2_w = max(45, max(len(b.get('text', '')) for b in bubbles))

    # Defining table drawing characters
    top_line = "╔" + "═" * (col1_w + 2) + "╦" + "═" * (col2_w + 2) + "╗"
    header_line = f"║ {'BUBBLE ID':<{col1_w}} ║ {'TEXTUAL CONTENT':<{col2_w}} ║"
    sep_line = "╠" + "═" * (col1_w + 2) + "╬" + "═" * (col2_w + 2) + "╣"
    bottom_line = "╚" + "═" * (col1_w + 2) + "╩" + "═" * (col2_w + 2) + "╝"

    print("\n" + top_line)
    print(header_line)
    print(sep_line)

    for b in bubbles:
        display_text = b.get('text', '')
        print(f"║ {b.get('bubble_id', ''):<{col1_w}} ║ {display_text:<{col2_w}} ║")

    print(bottom_line)
    print(f"Total text bubbles detected: {len(bubbles)}\n")




def print_translations_to_console(bubbles: list[dict]):
    """
    Print the original source text alongside its localized translation to the console.

    Provides a clean, itemized list comparing the pre-translation English text
    with the target language output generated by the LLM.

    Args:
        bubbles (list[dict]): A list of bubble dictionaries containing 'bubble_id',
                              'text' (original), and 'translation'.
    """
    
    print("\n" + "="*25 + " BUBBLE TRANSLATIONS (CONSOLE) " + "="*25)
    for b in bubbles:
        print(f"Bubble ID #{b['bubble_id']}:")
        print(f"  [ORIGINAL EN]: {b['text']}")
        print(f"  [TRANSLATION]: {b.get('translation', '--- NO TRANSLATION FOUND ---')}")
        print("-" * 70)
    print("="*80 + "\n")
