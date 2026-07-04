
# backend/utils/typesetting.py

"""
Handles text wrapping, word breaking, and space aware typesetting for manga speech bubbles.

This module is responsible for ensuring that translated text fits naturally within
the borders of actual, physical speech bubbles (rather than logical clusters grouped
solely based on text proximity).

Core functionalities:
1. Bubble Interior Detection: Uses a morphological flood-fill algorithm starting
   from the OCR text centroid to map the exact physical boundaries (walls) of the
   speech bubble, ignoring dark artwork and panel borders.
2. Dynamic Text Formatting: Splits, wraps, and hyphenates words using Pyphen.
   Crucially, this is configured specifically to respect Polish hyphenation rules
   (using the 'pl_PL' dictionary pattern) to ensure that the newly wrapped text
   remains linguistically correct and visually balanced within the bubble's geometry.
"""

from typing import List, Tuple, Optional
from PIL import Image, ImageDraw, ImageFont
import cv2
import numpy as np
from core.config import TYPESETTING_PADDING_RATIO
import pyphen


_hyphen_dict = pyphen.Pyphen(lang='pl_PL')

def detect_bubble_interior(
    image_bgr: np.ndarray,
    seed_point: Tuple[int, int],
    ocr_bbox: Tuple[int, int, int, int],
    dark_thresh: int = 180,
    border_dilate: int = 2,
    max_growth: float = 2.5
) -> Tuple[Optional[np.ndarray], Optional[Tuple[int, int, int, int]]]:
    """
    Detects the actual speech bubble boundary using the flood-fill algorithm
    on the ORIGINAL (uncleaned) image.

    Instead of looking for bright areas, this approach treats all dark pixels
    (text lines, comic frame borders, panel artwork) as "walls" and performs
    morphological dilation to ensure the boundary walls are thick and leak-proof
    before starting the flood fill.

    VISUAL EXAMPLE: THE "LEAKY BUBBLE" PROBLEM
    Manga speech bubbles often have thin, sketchy, or slightly broken borders.

    Without Dilation (Flood Fill Leaks):
      [ ] [ ] [#] [ ] [ ]      <- A 1-pixel gap in the bubble wall!
      [ ] [#] [T] [#] [ ]      <- Fill starts at 'T' (Text seed)
      [ ] [#] [ ] [#] [ ]      <- Fill leaks through the top gap and floods the page!

    With Dilation (Walls are artificially thickened):
      [ ] [#] [#] [#] [ ]      <- The gap is successfully sealed by dilation!
      [#] [#] [T] [#] [#]      <- Fill is perfectly contained inside the bubble.
      [#] [#] [ ] [#] [#]

    TODO: INPAINTING SYNCHRONIZATION
    Currently, this flood-fill mask is used strictly to calculate the writable
    bounding box for typesetting. In future iterations, this generated mask
    should be exported and shared with the inpainting/cleaning module.
    By confining the inpainting model strictly to this exact bubble interior,
    we can prevent the AI from accidentally erasing or hallucinating manga
    lineart that sits just outside the bubble.

    :param image_bgr: The original, uncleaned BGR image.
    :param seed_point: (x, y) starting coordinate (e.g., centroid of the OCR text).
    :param ocr_bbox: (x1, y1, x2, y2) bounding box of the OCR text group.
    :param dark_thresh: Threshold to separate "dark" walls from "light" background.
    :param border_dilate: How many pixels to expand the dark lines to seal gaps.
    :param max_growth: Maximum allowed area ratio compared to the initial OCR box.
    """
    # Convert BGR image to grayscale
    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape

    # 1. "Walls" definition = anything dark. This includes the bubble outline AND the text
    # (it makes no difference whether the text was fully detected by OCR or not).
    dark_mask = (gray < dark_thresh).astype(np.uint8) * 255

    # 2. Thicken the walls — dilating the dark mask makes the contours thicker,
    # ensuring the flood fill will never accidentally pierce or "eat" through thin lines.
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (border_dilate * 2 + 1,) * 2)
    walls = cv2.dilate(dark_mask, kernel, iterations=1)

    # Relocate seed point to the nearest free pixel outside the thickened walls
    seed_point = _nearest_free_pixel(walls, *seed_point)
    if seed_point is None:
        print(f"[BUBBLE FLOODFILL] Seed point at {seed_point} is on or too close to a wall — no free space nearby.")
        return None, None

    # 3. Setup floodFill with a blocking mask — according to OpenCV documentation,
    # non-zero pixels inside the mask parameter act as physical barriers that block the filling process.
    flood_mask = np.zeros((h + 2, w + 2), np.uint8)
    flood_mask[1:-1, 1:-1] = (walls > 0).astype(np.uint8)

    # Perform flood fill using the barrier mask
    filled = np.zeros_like(gray)
    cv2.floodFill(filled, flood_mask, seed_point, 255)

    # Convert the flooded area into the final binary bubble mask
    bubble_mask = (filled == 255).astype(np.uint8) * 255
    contours, _ = cv2.findContours(bubble_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None, None

    # Select the largest connected filled area to ignore small noise artifacts
    largest = max(contours, key=cv2.contourArea)
    x, y, bw, bh = cv2.boundingRect(largest)

    # 4. GUARD RAIL: Ensure the flood fill didn't leak into the background artwork or entire page.
    ocr_x1, ocr_y1, ocr_x2, ocr_y2 = ocr_bbox
    ocr_w, ocr_h = max(1, ocr_x2 - ocr_x1), max(1, ocr_y2 - ocr_y1)
    limit_w, limit_h = ocr_w * max_growth, ocr_h * max_growth
    min_w, min_h = ocr_w * 0.9, ocr_h * 0.9

    if bw > limit_w or bh > limit_h:
        print(f"[BUBBLE GUARD] Flood fill escaped bubble boundaries: detected {bw}x{bh}px, "
              f"limit {limit_w:.0f}x{limit_h:.0f}px (OCR box was {ocr_w}x{ocr_h}px). "
              f"Falling back to OCR bounding box.")
        return None, None

    if bw < min_w or bh < min_h:
        print(f"[BUBBLE GUARD] Flood fill trapped in an excessively small area: detected {bw}x{bh}px, "
              f"minimum {min_w:.0f}x{min_h:.0f}px (OCR box was {ocr_w}x{ocr_h}px, seed={seed_point}). "
              f"Falling back to OCR bounding box.")
        return None, None

    return bubble_mask, (x, y, x + bw, y + bh)


def _nearest_free_pixel(
        walls: np.ndarray,
        cx: float,
        cy: float,
        max_radius: int = 60,
        step: int = 5
) -> Optional[Tuple[int, int]]:
    """
    Finds the nearest free pixel (0 value on the walls image) close to the specified coordinates.

    This is critical because the centroid of an OCR bounding box often falls
    directly on a black character stroke (thickened wall), which would otherwise
    fail to flood-fill the interior of the bubble.

    :param walls: Binary walls mask where thickened barriers are non-zero.
    :param cx: Target X coordinate (e.g. OCR center).
    :param cy: Target Y coordinate (e.g. OCR center).
    :param max_radius: Maximum radial distance (in pixels) to search.
    :param step: Grid search step resolution.
    :return: Safest (X, Y) coordinate representing a free pixel, or None if not found.
    """
    h, w = walls.shape
    cx, cy = int(cx), int(cy)

    # Return immediately if the start coordinate is already located in free space
    if 0 <= cy < h and 0 <= cx < w and walls[cy, cx] == 0:
        return (cx, cy)

    # Search outwards in concentric square rings
    for r in range(step, max_radius, step):
        directions = [
            (-r, 0), (r, 0), (0, -r), (0, r),
            (-r, -r), (r, r), (-r, r), (r, r)
        ]
        for dx, dy in directions:
            nx, ny = cx + dx, cy + dy
            if 0 <= nx < w and 0 <= ny < h and walls[ny, nx] == 0:
                return (nx, ny)

    return None


def _clamp_bbox(x1, y1, x2, y2, img_w, img_h, min_size=15):
    """
    Clamps the bounding box coordinates to the actual boundaries of the image canvas
    we are drawing on to prevent out-of-bounds rendering exceptions.
    """
    cx1 = max(0, min(x1, img_w - 1))
    cy1 = max(0, min(y1, img_h - 1))
    cx2 = max(0, min(x2, img_w))
    cy2 = max(0, min(y2, img_h))

    if cx2 - cx1 < min_size or cy2 - cy1 < min_size:
        return None
    return cx1, cy1, cx2, cy2


def _syllables(word: str) -> List[str]:
    """Splits a word into syllables based on Polish hyphenation rules."""
    # Use a null byte (\x00) as a temporary delimiter for hyphenation points
    hyphenated = _hyphen_dict.inserted(word, hyphen="\x00")
    return hyphenated.split("\x00")

def _split_chars_to_fit(word: str, font: ImageFont.ImageFont, max_w: int) -> List[str]:
    """
    Brutal fallback: splits character-by-character when even a single syllable
    is too wide for the target width.
    """
    chunks = []
    current = ""
    for ch in word:
        test = current + ch
        # Calculate width using the bounding box
        w = font.getbbox(test)[2] - font.getbbox(test)[0]

        # If it fits, or if the current buffer is empty (to prevent infinite loops), accumulate
        if w <= max_w or not current:
            current = test
        else:
            chunks.append(current)
            current = ch

    if current:
        chunks.append(current)
    return chunks


def _split_word_to_fit(
        word: str,
        font: ImageFont.ImageFont,
        max_w: int) -> List[str]:
    """
    Splits an oversized word into fragments that fit within max_w.

    First, it attempts to split at syllable boundaries according to Polish rules
    (appending a hyphen at the end of each fragment except the last) — resulting
    in proper typographic layout.

    Falls back to character-by-character splitting only if the word has no recognized
    syllables (e.g., foreign proper nouns, digit strings) or if a single syllable
    itself is wider than max_w.
    """
    # Guard clause for invalid or zero width bounding box
    if max_w <= 0:
        return [word]

    word_w = font.getbbox(word)[2] - font.getbbox(word)[0]
    if word_w <= max_w:
        return [word]

    syllables = _syllables(word)
    chunks = []
    current = ""

    # Attempt syllable-based hyphenation
    for i, syl in enumerate(syllables):
        is_last = (i == len(syllables) - 1)

        # Test line width including the hyphen character if it's not the last syllable
        candidate = current + syl + ("" if is_last else "-")
        cw = font.getbbox(candidate)[2] - font.getbbox(candidate)[0]

        if cw <= max_w or not current:
            current += syl
        else:
            # Commit current chunk with a trailing hyphen and start a new one
            chunks.append(current + "-")
            current = syl

    if current:
        chunks.append(current)

    # Emergency fallback: if any syllable fragment is still too wide, split char-by-char
    final_chunks = []
    for chunk in chunks:
        cw = font.getbbox(chunk)[2] - font.getbbox(chunk)[0]
        if cw <= max_w:
            final_chunks.append(chunk)
        else:
            # Character-by-character split for extreme cases
            final_chunks.extend(_split_chars_to_fit(chunk, font, max_w))

    return final_chunks


def draw_translated_text_on_clean_image(
        image_clean: np.ndarray,
        image_original: np.ndarray,
        bubbles: List[dict],
        to_original_coords
) -> np.ndarray:
    """
    Draws translated text using the Pillow (PIL) library.

    Provides full, native support for Polish diacritic characters (ą, ć, ę, ł, ń, ó, ś, ź, ż),
    enforces UPPERCASE formatting typical for manga lettering, and strictly respects
    the detected bubble boundaries (bounding box guard).
    """
    img_rgb = cv2.cvtColor(image_clean, cv2.COLOR_BGR2RGB)
    img_h, img_w = image_clean.shape[:2]
    pil_img = Image.fromarray(img_rgb)
    draw = ImageDraw.Draw(pil_img)

    # Font path (best to provide a path to a downloaded .ttf comic font)(TODO in future)
    font_path = "arial.ttf"

    for b in bubbles:
        text = (b.get("translation") or b["text"]).strip().upper()
        orig_box = to_original_coords(b["box_coords"])
        xs = [p[0] for p in orig_box]
        ys = [p[1] for p in orig_box]
        cx, cy = int(sum(xs) / len(xs)), int(sum(ys) / len(ys))

        ocr_bbox = (int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys)))
        print(f"[BUBBLE RAW] bubble_id={b.get('bubble_id')}: ocr_bbox={ocr_bbox}, "
              f"seed=({cx},{cy}), canvas={img_w}x{img_h}")
        mask, bbox = detect_bubble_interior(image_original, (cx, cy), ocr_bbox)

        if bbox:
            bx1, by1, bx2, by2 = bbox

            print(f"[BUBBLE FLOOD RESULT] bubble_id={b.get('bubble_id')}: "
                  f"floodfill bbox=({bx1},{by1},{bx2},{by2}) size={bx2 - bx1}x{by2 - by1}, "
                  f"ocr_bbox_size={ocr_bbox[2] - ocr_bbox[0]}x{ocr_bbox[3] - ocr_bbox[1]}")

            mx = int((bx2 - bx1) * 0.10)
            my = int((by2 - by1) * 0.10)
            x1, y1, x2, y2 = bx1 + mx, by1 + my, bx2 - mx, by2 - my
        else:
            x1, x2 = ocr_bbox[0], ocr_bbox[2]
            y1, y2 = ocr_bbox[1], ocr_bbox[3]
            w, h = x2 - x1, y2 - y1
            x1 -= int(w * 0.175)
            x2 += int(w * 0.175)
            y1 -= int(h * 0.20)
            y2 += int(h * 0.20)

        # Uniform margin — the same rule for both paths, a single knob for tuning
        pad_x = int((x2 - x1) * TYPESETTING_PADDING_RATIO)
        pad_y = int((y2 - y1) * TYPESETTING_PADDING_RATIO)
        x1 += pad_x
        x2 -= pad_x
        y1 += pad_y
        y2 -= pad_y

        # Enforce boundary clamping for both branches (flood-fill success or fallback expansion)
        raw_x1, raw_y1, raw_x2, raw_y2 = x1, y1, x2, y2
        clamped = _clamp_bbox(x1, y1, x2, y2, img_w, img_h)

        if clamped is None:
            print(f"[BUBBLE CLAMP FAIL] bubble_id={b.get('bubble_id')}: "
                  f"bbox ({raw_x1},{raw_y1},{raw_x2},{raw_y2}) głównie poza canvasem "
                  f"{img_w}x{img_h} — pomijam dymek całkowicie: {text!r}")
            continue

        x1, y1, x2, y2 = clamped

        if (x1, y1, x2, y2) != (raw_x1, raw_y1, raw_x2, raw_y2):
            print(f"[BUBBLE CLAMP] bubble_id={b.get('bubble_id')}: "
                  f"({raw_x1},{raw_y1},{raw_x2},{raw_y2}) -> ({x1},{y1},{x2},{y2}) "
                  f"[canvas {img_w}x{img_h}]")

        target_w, target_h = x2 - x1, y2 - y1
        draw_x1, draw_y1 = x1, y1

        if target_w <= 10 or target_h <= 10:
            print(f"[BUBBLE SKIP] bubble_id={b.get('bubble_id')}: too small after clamping "
                  f"({target_w}x{target_h}px) — text will NOT be drawn: {text!r}")
            continue

        words = text.split()
        if not words:
            continue

        font_size = 40  # Starting, large font size
        min_font_size = 11  # Lowest readable font size before giving up wrapping
        success = False
        final_lines = []
        final_font = None
        final_line_h = 0
        final_gap = 0

        # Algorithm for adjusting size and breaking lines
        while font_size > 8:
            try:
                font = ImageFont.truetype(font_path, font_size)
            except IOError:
                font = ImageFont.load_default()

            lines = []
            current_line = []
            word_too_long = False

            # Try wrapping text for the current font_size
            for word in words:
                # Check if a single word isn't wider than the whole bubble!
                word_w = font.getbbox(word)[2] - font.getbbox(word)[0]
                if word_w > target_w:
                    word_too_long = True
                    break  # We must use a smaller font because this word overflows!

                test_line = " ".join(current_line + [word]) if current_line else word
                line_w = font.getbbox(test_line)[2] - font.getbbox(test_line)[0]

                if line_w <= target_w:
                    current_line.append(word)
                else:
                    if current_line:
                        lines.append(" ".join(current_line))
                        current_line = [word]
                    else:
                        lines.append(word)
                        current_line = []

            if word_too_long:
                font_size -= 2
                continue  # If any word breaks the box, decrease font and restart the loop

            if current_line:
                lines.append(" ".join(current_line))

            sample_bbox = font.getbbox("Ay_gżł")
            line_h = sample_bbox[3] - sample_bbox[1]
            gap = int(font_size * 0.15)
            total_text_h = (len(lines) * line_h) + ((len(lines) - 1) * gap)

            # Check if it fits vertically
            if total_text_h <= target_h:
                final_lines = lines
                final_font = font
                final_line_h = line_h
                final_gap = gap
                success = True
                break

            font_size -= 2  # If it doesn't fit vertically, decrease and restart

        # Fallback branch: Word wrapping using minimum readable font size
        ABSOLUTE_MIN_FONT_SIZE = 7

        if not success:
            font_size = min_font_size
            while True:
                try:
                    final_font = ImageFont.truetype(font_path, font_size)
                except IOError:
                    final_font = ImageFont.load_default()

                final_lines = _wrap_text_with_splitting(words, final_font, target_w)

                sample_bbox = final_font.getbbox("Ay_gżł")
                final_line_h = sample_bbox[3] - sample_bbox[1]
                final_gap = max(1, int(font_size * 0.15))
                total_text_h = (len(final_lines) * final_line_h) + ((len(final_lines) - 1) * final_gap)

                if total_text_h <= target_h or font_size <= ABSOLUTE_MIN_FONT_SIZE:
                    if total_text_h > target_h:
                        print(f"[BUBBLE OVERFLOW] bubble_id={b.get('bubble_id')}: text does not fit "
                              f"even at {font_size}px ({total_text_h}px > {target_h}px bubble height). "
                              f"Text: {text!r}")
                    break

                font_size -= 1

        # Drawing vertically centered text using expanded coordinates
        y_offset = draw_y1 + max(0, (target_h - total_text_h) // 2)

        for line in final_lines:
            bbox = final_font.getbbox(line)
            line_w = bbox[2] - bbox[0]
            # Horizontal centering within the expanded box
            x_offset = draw_x1 + max(0, (target_w - line_w) // 2)

            draw.text((x_offset, y_offset), line, fill=(0, 0, 0), font=final_font)
            y_offset += final_line_h + final_gap

    return cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)


def _wrap_text_with_splitting(words, font, target_w):
    """Wraps words to fit target_w, breaking down individual words that are too long into pieces."""
    lines = []
    current_line = []

    for word in words:
        # Calculate the width of the current word using its bounding box
        word_w = font.getbbox(word)[2] - font.getbbox(word)[0]

        # Case 1: The single word itself is wider than the target width
        if word_w > target_w:
            if current_line:
                lines.append(" ".join(current_line))
                current_line = []
            # Force split the oversized word and add the pieces directly to lines
            lines.extend(_split_word_to_fit(word, font, target_w))
            continue

        # Case 2: Test if the word fits into the current line
        test_line = " ".join(current_line + [word]) if current_line else word
        line_w = font.getbbox(test_line)[2] - font.getbbox(test_line)[0]

        if line_w <= target_w:
            # The word fits perfectly, append it to the current line
            current_line.append(word)
        else:
            # The word doesn't fit, so we close the current line (if it exists)
            if current_line:
                lines.append(" ".join(current_line))
                current_line = [word]  # Start a new line with the current word
            else:
                # Fallback: if the current line was empty, just append the word as a full line
                lines.append(word)
                current_line = []

    # Append any remaining text left in the buffer
    if current_line:
        lines.append(" ".join(current_line))

    return lines
