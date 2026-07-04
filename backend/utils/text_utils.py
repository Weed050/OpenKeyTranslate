
# backend/utils/text_utils.py

"""
Provides foundational text processing, validation, and parsing utilities for the OCR pipeline.

This module acts as a centralized utility hub. By isolating these core helper functions
(such as marker detection, text normalization, and linguistic validation) into a standalone
file, it prevents circular dependency issues (circular imports) between higher-level
modules like `marker.py`, `merge_boxes.py`, and `ocr_pipeline.py` which all rely on this shared logic.

Key Responsibilities:
1. Marker Detection: Generates robust regex patterns that account for common OCR visual
   misreads (e.g., mistaking a '1' for an 'I') to reliably identify alignment markers.
2. Linguistic Validation: Uses Zipf frequency analysis to distinguish real, dictionary-valid
   words from random OCR artifact noise.
3. Threshold & Bypass Logic: Parses raw PaddleOCR output and decides which text bounding
   boxes to keep based on hard confidence scores, "soft tolerance" linguistic rescues,
   and strict bypasses for critical layout markers.
"""

import re
import string
from wordfreq import zipf_frequency

from core.config import SOFT_TOLERANCE

# Common OCR digit misread substitutions
_DIGIT_ALTS = {
    "0": "[0Oo]",
    "1": "[1Il]",
    "5": "[5Ss]",
    "8": "[8Bb]",
}

# Single characters that are legally allowed to exist standalone in English text.
# Prevents single-letter OCR noise (like a random 'C' or 'X' from background art)
# from being validated as actual text.
_SINGLE_CHAR_WHITELIST = {"I"} #"A"


def _normalize_ocr_text(text: str) -> str:
    """
    Strip visual noise and structural formatting from OCR text.

    Removes all whitespace and non-alphanumeric characters. This allows for
    consistent and aggressive pattern matching, ensuring that artifacts like
    '_ M 1 _' or '-M1-' are correctly evaluated as the marker 'M1'.
    """
    t = text.strip()
    t = re.sub(r"[\s]+", "", t)
    t = re.sub(r"[^a-zA-Z0-9]", "", t)
    return t


def marker_pattern(slice_id: int, anchored: bool = True) -> re.Pattern:
    """
    The single source of truth defining the slice marker regex pattern.

    Dynamically builds a regex pattern that accounts for common OCR visual
    misreads based on the predefined `_DIGIT_ALTS` dictionary.

    :param slice_id: The ID of the current slice to match (e.g., 1 for '__M1__').
    :param anchored: If True, wraps the pattern with ^ and $ for exact matching
                     to prevent false positives (e.g., preventing M1 from matching M11).
    """
    digits = str(slice_id)
    pattern_digits = "".join(_DIGIT_ALTS.get(d, d) for d in digits)
    body = rf"M{pattern_digits}"
    return re.compile(rf"^{body}$" if anchored else body, re.IGNORECASE)


def is_any_marker(text: str, slice_id: int | None = None) -> bool:
    """
    Check if the given text represents an injected layout marker.

    If slice_id is provided, performs a strict check for that specific slice's marker.
    Otherwise, performs a generic fallback check for any marker pattern (M followed by digits).
    """
    normalized = _normalize_ocr_text(text)
    if slice_id is not None:
        return bool(marker_pattern(slice_id).fullmatch(normalized))
    return bool(re.search(r"M\d+", normalized, re.IGNORECASE))


def _is_plausible_text(text: str, lang: str = "en", min_zipf: float = 2.0) -> bool:
    """
    Determine if a piece of text is linguistically plausible using dictionary lookups.

    This function acts as a safety net against OCR hallucinations. It filters out
    standalone single-letter noise using a whitelist, and evaluates longer phrases
    by calculating the ratio of recognized dictionary words using Zipf frequency.
    """
    clean = text.strip()

    # Handle standalone single-character noise
    if len(clean) == 1:
        return clean.upper() in _SINGLE_CHAR_WHITELIST

    words = clean.split()
    if not words:
        return False

    # Count how many words meet the minimum frequency threshold in the given language
    recognized = sum(
        zipf_frequency(w.strip(string.punctuation).lower(), lang) >= min_zipf
        for w in words
    )

    # Accept the text chunk if at least 50% of its words are recognized as real language
    return recognized / len(words) >= 0.5


def passes_text_threshold(text: str, score: float, threshold: float) -> bool:
    """
    Evaluate whether an OCR text item should be kept based on its confidence score
    and linguistic plausibility.

    Logic flow:
    - PASS: Score is above the strict hard threshold.
    - PASS (Rescue): Score is below the hard threshold but within the SOFT_TOLERANCE
      margin, AND the text represents real language (plausible dictionary words).
    - FAIL: Score is too low, or it's within the soft tolerance but looks like gibberish.

    TODO:
    This dictionary-based rescue method has a known limitation with text that is
    deliberately distorted for emotional or dramatic effect (very common in manga,
    e.g., "...LIEUTENANT ...LIEUTENANT ...LAN...INTH!"). Currently, the underlying
    `_is_plausible_text` check will classify these highly stylized expressions as
    noise and falsely reject them. This edge case needs to be addressed in future
    iterations.
    """
    if score >= threshold:
        return True
    if score >= threshold - SOFT_TOLERANCE:
        return _is_plausible_text(text)
    return False


# ----------------- PACKAGING OCR RESULTS -----------------

def parse_ocr_results(result, slice_id: int, threshold: float) -> list[dict]:
    """
    Convert raw PaddleOCR prediction outputs into a standardized list of dictionaries.

    Role in the system:
    This is the primary gateway filtering raw OCR engine output before it enters
    the deduplication and typesetting pipelines. It actively discards low-confidence
    noise while enforcing critical bypass rules (e.g., injected markers are always
    extracted regardless of their OCR confidence score to prevent layout sync loss).

    :param result: Raw list of predictions returned by PaddleOCR.
    :param slice_id: The identifier of the slice being processed.
    :param threshold: The strict confidence score threshold.
    :return: A standardized list of items with keys: 'text', 'score', 'box', 'slice_id'.
    """
    items = []
    if not result:
        return items

    for res in result:
        for t, s, b in zip(res["rec_texts"], res["rec_scores"], res["dt_polys"]):

            # CRITICAL BYPASS: Injected layout markers often receive naturally low
            # OCR scores because they look like artificial noise in a white padded zone.
            # We MUST bypass standard score thresholds to extract them, otherwise
            # spatial rotation correction and downstream inpainting will critically fail.
            if not is_any_marker(t, slice_id) and not passes_text_threshold(t, s, threshold):
                continue

            items.append({
                "text": t,
                "score": s,
                "box": b,
                "slice_id": slice_id
            })
    return items