
# backend/utils/draw_debug.py

"""
OpenCV Visual Debugging Module
______________________________

This file contains a collection of functions that use OpenCV to draw directly
onto the image for debugging purposes. It helps developers visually verify
what the pipeline is doing under the hood by showing exactly:

  - Where the raw OCR bounding boxes and text are detected.
  - Where the image is being cut into overlapping horizontal slices.
  - Where individual text boxes are grouped together into lines and final speech bubbles.
  - Where the tracking markers are injected, detected, and moved (to validate rotation and padding).
"""

import cv2
import numpy as np


def draw_text_boxes(items, image, to_original_coords):
    """
    Visualize raw OCR results by drawing bounding boxes and text overlays on original image.

    Iterates through OCR items, mapping their scaled bounding boxes back to the
    original image dimensions. Draws a red bounding polygon and overlays the
    recognized text just above the box.

    Args:
        items (list[dict]): The list of dictionaries returned by the OCR engine.
        image (np.ndarray): The source image in its original scale/resolution.
        to_original_coords (callable): Function that maps bounding box coordinates
                                       (e.g., [[x1,y1], [x2,y2], ...]) back to the original scale.

    Returns:
        np.ndarray: The original image matrix with drawn OCR overlays.
    """
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



def draw_slices(image, slices, scale):
    """
    Draw slice boundary rectangles with alternating colors for visual debugging.

    Renders horizontal slicing boundaries on the image to help visualize how the
    image was partitioned during the OCR pipeline. Each slice is given a distinct
    color and a designated 'S#' label.

    Args:
        image (np.ndarray): The image array to draw upon (original scale).
        slices (list[tuple[int, int]]): A list of (y_start, y_end) tuples defining
                                        the vertical bounds of each slice.
        scale (float): The scale multiplier used during OCR processing.

    Returns:
        np.ndarray: The image matrix with slice boundaries drawn.
    """

    h, w = image.shape[:2]

    colors = [
        (255, 0, 0),    # blue
        (0, 255, 0),    # green
        (0, 0, 255),    # red
        (255, 255, 0),  # cyan
        (255, 0, 255),  # magenta
        (0, 255, 255),  # yellow
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

        # Slice identifier label
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



def draw_merged_lines(image, grouped_lines, scale_fn, color=(0, 255, 0), thickness=3):
    """
    Draw bounding boxes around grouped text boxes into lines for diagnostic and debugging purposes.

    Visualizes how individual OCR word boxes have been merged into cohesive horizontal
    or vertical lines. Overlays the calculated line ID above each bounding box.

    Args:
        image (np.ndarray): Target image to draw on (original scale).
        grouped_lines (list[dict]): List of dictionaries representing grouped lines
                                    (output from build_text_lines).
        scale_fn (callable): The to_original_coords function used to remap dimensions
                             back to the source scale.
        color (tuple): BGR color tuple for the bounding box (defaults to green).
        thickness (int): Thickness of the bounding box lines.

    Returns:
        np.ndarray: The image matrix with merged line overlays.
    """
    for g_line in grouped_lines:
        # Remap bounding box coordinates back to the original image scale
        orig_box = scale_fn(g_line["box"])

        # Prepare coordinate points for OpenCV
        # Canonical Paddle format follows: [[x1,y1], [x2,y1], [x2,y2], [x1,y2]]
        pts = np.array(orig_box, dtype=np.int32)

        # Draw the contour (polylines correctly accommodates potentially skewed/rotated boxes)
        cv2.polylines(image, [pts], isClosed=True, color=color, thickness=thickness)

        # Overlay a brief label depicting the line ID slightly above the bounding box
        x1, y1 = pts[0]
        cv2.putText(image, g_line["line_id"], (x1, y1 - 10),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)

    return image



def draw_bubbles(image, bubbles, scale_fn, color=(255, 0, 255), thickness=4):
    """
    Draw bounding boxes around grouped text_boxes into text_bubbles for diagnostic and debugging purposes.

    Visualizes the final stage of layout analysis, where related text lines
    have been grouped into "speech bubbles" or paragraphs.

    Args:
        image (np.ndarray): Target image to draw on (original scale).
        bubbles (list[dict]): List of dictionaries representing consolidated text bubbles
                              (output from group_lines_into_bubbles).
        scale_fn (callable): The to_original_coords function used to remap dimensions
                             back to the source scale.
        color (tuple): BGR color tuple for the bounding box (defaults to magenta).
        thickness (int): Thickness of the bounding box lines.

    Returns:
        np.ndarray: The image matrix with bubble overlays.
    """
    for b in bubbles:
        orig_box = scale_fn(b["box_coords"])

        # Shape adjustment explicitly required for cv2.polylines constraints: (N, 1, 2)
        pts = np.array(orig_box, dtype=np.int32).reshape((-1, 1, 2))

        cv2.polylines(image, [pts], isClosed=True, color=color, thickness=thickness)

        # Extract coordinates from the nested array layout: pts[0] represents [[x, y]]
        x1, y1 = pts[0][0]
        label = f"{b['bubble_id']} ({b['line_count']} lines)"
        cv2.putText(image, label, (int(x1), int(y1) - 15),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)

    return image




def make_marker_record(slice_id: int, y_start: int, marker_pos: tuple,
                       marker_item: dict | None, angle: int) -> dict:
    """
    Make a marker record for slice rotation tracking.

    This function is called once per slice inside `run_ocr_sliced`
    to encapsulate metadata required by `draw_marker_debug`.

    Args:
        slice_id (int): The index of the current horizontal slice.
        y_start (int): The starting Y-coordinate of the slice in scaled space.
        marker_pos (tuple): The (x, y) coordinates where the marker was injected.
        marker_item (dict | None): The OCR data item corresponding to the detected marker.
        angle (int): The calculated rotation angle that was applied (0 means no rotation).

    Returns:
        dict: Diagnostic record filled with data (about marker) ready to be appended to the tracking list.
    """
    marker_item_global = None
    if marker_item is not None:
        shifted_box = [[px, py + y_start] for px, py in marker_item["box"]]
        marker_item_global = {**marker_item, "box": shifted_box}

    return {
        "slice_id":    slice_id,
        "y_start":     y_start,
        "marker_pos":  marker_pos,
        "marker_item": marker_item_global,
        "angle":       angle,
    }



def draw_marker_debug(image: np.ndarray, marker_records: list, scale: float) -> np.ndarray:
    """
    Draw comprehensive marker diagnostics and rotation information on the image.

    For every processed slice, this function draws on image:
      - GREEN cross+circle : The exact position where the tracking marker was injected.
      - ORANGE             : The actual bounding box of the marker detected by the OCR.
      - RED cross          : The computed geometric center of the detected marker box.
      - YELLOW line        : A vector tracking the shift from injected to detected center.
      - CYAN label         : Text showing the slice ID, detected text, and applied angle.

    Args:
        image (np.ndarray): Original-scale image (modified in-place).
        marker_records (list[dict]): List of tracking records generated by make_marker_record().
        scale (float): The scale multiplier used during OCR processing.

    Returns:
        np.ndarray: The original image matrix with drawn marker debug indicators.
    """
    h, w = image.shape[:2]

    COLOR_EXPECTED = (0,   200,   0)
    COLOR_DETECTED = (0,   140, 255)
    COLOR_CENTER   = (0,     0, 255)
    COLOR_LINE     = (0,   255, 255)
    COLOR_LABEL    = (255, 255,   0)
    CROSS_SIZE     = 12

    def _cross(img: np.ndarray, cx: int, cy: int, size: int, color: tuple, thickness: int = 2):
        """Draw a custom crosshair at the specified coordinates within image bounds."""
        cx, cy = max(0, min(int(cx), w - 1)), max(0, min(int(cy), h - 1))
        cv2.line(img, (cx - size, cy), (cx + size, cy), color, thickness)
        cv2.line(img, (cx, cy - size), (cx, cy + size), color, thickness)

    def _clamp(pts: np.ndarray, iw: int, ih: int) -> np.ndarray:
        """
        Clamp polygon coordinates to ensure they stay strictly within image boundaries.

        Note:
            Markers can naturally be drawn outside the original image dimensions
            due to the injected 'WHITE_SPACE' padding. This function safely forces
            those outlier coordinates back into the valid canvas array bounds to
            prevent out-of-bounds pixel mapping errors in OpenCV or smth similar.
        """
        pts[..., 0] = np.clip(pts[..., 0], 0, iw - 1)
        pts[..., 1] = np.clip(pts[..., 1], 0, ih - 1)
        return pts

    def _text(img: np.ndarray, txt: str, x: int, y: int):
        """Draw text on the image, truncating the string if it exceeds the right margin."""
        x, y = max(0, min(int(x), w - 1)), max(1, min(int(y), h - 1))
        avail = w - x
        while txt:
            (tw, _), _ = cv2.getTextSize(txt, cv2.FONT_HERSHEY_SIMPLEX, 0.45, 1)
            if tw <= avail:
                break
            txt = txt[:-1]
        if txt:
            cv2.putText(img, txt, (x, y), cv2.FONT_HERSHEY_SIMPLEX, 0.45, COLOR_LABEL, 1)

    for rec in marker_records:
        mx_local, my_local = rec["marker_pos"]
        ex = int(mx_local / scale)
        ey = int((my_local + rec["y_start"]) / scale)
        ex, ey = max(0, min(ex, w - 1)), max(0, min(ey, h - 1))

        _cross(image, ex, ey, CROSS_SIZE, COLOR_EXPECTED, 2)
        cv2.circle(image, (ex, ey), CROSS_SIZE + 4, COLOR_EXPECTED, 1)

        label_parts = [f"S{rec['slice_id']}"]
        dx, dy = ex, ey

        if rec["marker_item"] is not None:
            pts = np.array(
                [[int(px / scale), int(py / scale)] for px, py in rec["marker_item"]["box"]],
                dtype=np.int32
            ).reshape((-1, 1, 2))
            pts = _clamp(pts, w, h)
            cv2.polylines(image, [pts], isClosed=True, color=COLOR_DETECTED, thickness=2)
            dx, dy = int(np.mean(pts[:, 0, 0])), int(np.mean(pts[:, 0, 1]))
            _cross(image, dx, dy, CROSS_SIZE - 4, COLOR_CENTER, 2)
            cv2.line(image, (ex, ey), (dx, dy), COLOR_LINE, 1)
            label_parts.append(rec["marker_item"].get("text", "?"))
        else:
            label_parts.append("NOT FOUND")

        label_parts.append(f"{rec['angle']}°")
        _text(image, "  ".join(label_parts), max(0, ex - 5), max(15, ey - CROSS_SIZE - 6))

    return image




def draw_marker_relocation_debug(image: np.ndarray, marker_records: list, scale: float) -> np.ndarray:
    """
    Draw another marker debug, but from function used to recover marker.

    Uses a distinct Bright Pink/Red color palette to differentiate from standard marker debug visuals.
    Visualizes marker shifts and potential padding displacements by drawing:
      - A Red tilted 'X': Designated default marker placement (injection) position.
      - A Bright Pink '+': Detected marker position after running the recovery logic.
      - A Bright Pink line: Connector tracking the displacement between the injection
                        position and the actual recovered coordinates.

    Args:
        image (np.ndarray): Target image array to draw on.
        marker_records (list[dict]): List of tracking records containing coordinate data.
        scale (float): The scale multiplier used during OCR processing.

    Returns:
        np.ndarray: The modified image (array).
    """
    # BGR Color Palette for relocation debug
    COLOR_ORIGINAL = (0, 0, 255)  # Red
    COLOR_RELOCATED = (255, 0, 255)  # Bright Pink (Magenta)

    for rec in marker_records:
        if rec["marker_item"] is None:
            continue

        mx_orig, my_orig = rec["marker_pos"]

        # Map injected default position back to the scaled image coordinates
        mx_scaled = int(mx_orig / scale)
        my_scaled = int((my_orig + rec["y_start"]) / scale)

        # Map detected position back to the scaled image coordinates
        if rec["marker_item"]["box"]:
            pts = rec["marker_item"]["box"]

            # Cast coordinates to float() to prevent unexpected numpy overflow conditions
            cx = int(sum(float(p[0]) for p in pts) / 4.0 / scale)
            cy = int(sum(float(p[1]) for p in pts) / 4.0 / scale)

            # Red tilted 'X' marker — signifies the original intended injection location
            cv2.drawMarker(image, (mx_scaled, my_scaled), COLOR_ORIGINAL, cv2.MARKER_TILTED_CROSS, 12, 2)

            # Bright Pink '+' marker — signifies the actual location recovered by the logic
            cv2.drawMarker(image, (cx, cy), COLOR_RELOCATED, cv2.MARKER_CROSS, 15, 2)
            cv2.putText(image, f"S{rec['slice_id']} Relocated", (cx + 5, cy - 5),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.4, COLOR_RELOCATED, 1)

            # Bright Pink line — visual connector tracking the relocation distance
            cv2.line(image, (mx_scaled, my_scaled), (cx, cy), COLOR_RELOCATED, 1)

    return image