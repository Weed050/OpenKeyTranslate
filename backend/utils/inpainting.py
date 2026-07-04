
#  backend/routers/ocr_utils/inpainting.py

"""
Provides image processing utilities for erasing original text from manga/webtoon panels.

Currently, this module relies on OpenCV's TELEA inpainting algorithm to fill in the
erased text areas based on surrounding pixels.

TODO:
- Quality Overhaul: The TELEA algorithm often smudges edges and produces lower-quality,
  blurry artifacts. It should be evaluated for replacement with a more advanced,
  deep-learning-based generative inpainting model (e.g., LaMa) in the future.
- Module Integration: This logic needs tighter integration with the bubble boundary
  detection found in `typesetting.py`. Instead of relying solely on OCR bounding boxes
  to create masks, it should leverage the precise flood-fill masks to ensure we
  don't accidentally smudge panel borders or character art.
"""

import numpy as np
import cv2


def erase_text_from_image(image_original, grouped_lines, to_original_coords):
    """
    Removes detected text from an image using OpenCV inpainting.

    This function generates a binary mask based on text bounding boxes, expands
    the mask slightly to catch edge artifacts, and smoothly fills the erased
    regions using surrounding pixel data.
    """
    # 1. Create a blank black mask matching the spatial dimensions (height, width) of the original image
    mask = np.zeros(image_original.shape[:2], dtype=np.uint8)

    # 2. Draw solid white polygons on the mask exactly where the text bounding boxes are located
    for line in grouped_lines:
        orig_box = to_original_coords(line["box"])
        pts = np.array(orig_box, np.int32).reshape((-1, 1, 2))
        cv2.fillPoly(mask, [pts], 255)

    # 3. Dilate (expand) the mask slightly to ensure we cover anti-aliasing and JPG artifacts around the letters
    kernel = np.ones((5, 5), np.uint8)
    mask = cv2.dilate(mask, kernel, iterations=1)

    # 4. Apply the TELEA inpainting algorithm to seamlessly reconstruct the image behind the masked areas
    cleaned_image = cv2.inpaint(image_original, mask, 5, cv2.INPAINT_TELEA)

    return cleaned_image