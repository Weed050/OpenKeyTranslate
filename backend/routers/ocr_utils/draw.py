import cv2
import numpy as np

def draw_text_boxes(items, image, to_original_coords):
    '''
    Visualize OCR results on an image.

    Draws bounding boxes and text labels using coordinates from OCR items.
    Uses `to_original_coords` to map boxes back to original image scale.
    :param items: items returned from ocr
    :param image: image in original scale
    :param to_original_coords: parameter: box = [[x1,y1], [x2,y2], ...]
    :return: image -
    '''
    for item in items:
        orig_box = to_original_coords(item["box"])
        pts = np.array(orig_box, np.int32).reshape((-1, 1, 2))

        cv2.polylines(image, [pts], True, (0, 0, 255), 2)

        x, y = orig_box[0]
        cv2.putText(
            image,
            item["text"],
            (int(x), int(y) - 5),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            (0, 0, 255),
            2
        )
    return image

def print_ocr_items_grouped(items, to_original_coords):
    print("\n--- OCR RESULTS ---")

    # grupowanie
    slices_map = {}
    for item in items:
        sid = item.get("slice_id", -1)
        slices_map.setdefault(sid, []).append(item)

    for sid in sorted(slices_map.keys()):
        print(f"\n[S{sid}]")

        # sortowanie po Y (czytelność jak tekst)
        slice_items = slices_map[sid]
        slice_items.sort(key=lambda x: to_original_coords(x["box"])[0][1])

        for item in slice_items:
            box = to_original_coords(item["box"])
            x, y = box[0]

            print(f"   {item['text']:<15} | score={item['score']:.2f} ({int(x)}, {int(y)})")


def draw_slices(image, slices, scale):
    """
    Draw slice rectangles with different colors for debugging.
    """

    h, w = image.shape[:2]

    colors = [
        (255, 0, 0),    # niebieski
        (0, 255, 0),    # zielony
        (0, 0, 255),    # czerwony
        (255, 255, 0),  # cyan
        (255, 0, 255),  # magenta
        (0, 255, 255),  # żółty
    ]

    for i, (y1, y2) in enumerate(slices):
        color = colors[i % len(colors)]

        y1_orig = int(y1 / scale)
        y2_orig = int(y2 / scale)

        cv2.rectangle(
            image,
            (0, y1_orig),
            (w, y2_orig),
            color,
            2
        )

        # podpis
        cv2.putText(
            image,
            f"S{i}",
            (10, y1_orig + 25),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            color,
            2
        )

    return image

