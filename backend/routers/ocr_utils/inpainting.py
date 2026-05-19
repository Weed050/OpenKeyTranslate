import cv2
import numpy as np


def erase_text_from_image(image_original, grouped_lines, to_original_coords):
    # 1. Tworzymy czarną maskę o wymiarach obrazka
    mask = np.zeros(image_original.shape[:2], dtype=np.uint8)

    # 2. Rysujemy na masce białe prostokąty tam, gdzie jest tekst
    for line in grouped_lines:
        orig_box = to_original_coords(line["box"])
        pts = np.array(orig_box, np.int32).reshape((-1, 1, 2))
        cv2.fillPoly(mask, [pts], 255)

    # 3. Delikatnie powiększamy maskę (Dilation), żeby objąć antyaliasing i artefakty JPG wokół liter
    kernel = np.ones((5, 5), np.uint8)
    mask = cv2.dilate(mask, kernel, iterations=1)

    # 4. Wykorzystujemy algorytm TELEA do zaszycia dziur
    cleaned_image = cv2.inpaint(image_original, mask, 5, cv2.INPAINT_TELEA)

    return cleaned_image