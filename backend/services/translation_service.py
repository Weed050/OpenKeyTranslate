
# backend/services/translation_service.py

"""
LLM Translation Service Module
______________________________

This module acts as the localization (translation) engine for the application,
bridging the gap between the extracted OCR text and a pluggable LLM translation
backend (see services/providers/). It is responsible for context-aware,
English-to-Polish translation of manga and comic speech bubbles.

Key Architectural Features:
    1. Pluggable Backend: Delegates the actual API call to whichever provider
       is configured as active (see core.config.ACTIVE_PROVIDER and
       services/providers/__init__.py). Swapping Groq for Gemini, or adding a
       new backend entirely, never requires touching this file.
    2. Batch Processing: Aggregates all text bubbles from a page into a single
       provider call. This drastically reduces network latency and token
       usage compared to translating bubbles one by one.
    3. Correction-Memory Injection: Before translating, each bubble's source
       text is checked against the project's correction history (see
       services/memory_service.py). A close-enough past correction is
       injected into the payload as a per-bubble "hint", nudging the model
       toward phrasing the user has already approved for similar text.
    4. Zero-Shot vs. Memory-Injected Logging: When a hint is used, the same
       bubble is additionally translated a second time without the hint,
       purely for logging (see models.models.TranslationLog). This produces
       the paired data the thesis experiment is evaluated from. Only the
       hinted result is ever shown to the user.
    5. Optional DB Context: project_id/page_id/db are optional. Pass them
       (from an active session) to enable correction-memory hints and A/B
       logging. Omit them for standalone runs with no DB-backed project -
       e.g. test_main.py's test_ocr_from_path() - translation still runs,
       just without memory or logging.
"""

import uuid
from sqlalchemy.orm import Session

from core.config import MEMORY_AB_TEST_LOGGING, ACTIVE_MODEL_NAME
from models.models import TranslationLog
from services.memory_service import find_best_match
from services.providers import get_provider

def translate_bubbles(
    bubbles: list[dict],
    project_id: int | None = None,
    page_id: int | None = None,
    db: Session | None = None,
) -> list[dict]:
    """
    Translate extracted speech bubble texts via the active LLM provider,
    injecting correction-memory hints where the project has a similar past
    correction.

    Params:
        bubbles (list[dict]): A list of bubble dictionaries. Each dictionary must
                              contain at least 'bubble_id' and 'text'.
        project_id (int | None): Used to scope correction-memory lookups to this
                          project (see services/memory_service.find_best_match).
                          Memory lookup is skipped entirely if None.
        page_id (int | None): Used to tag logged TranslationLog rows with their
                          source page. Required (together with db) for logging.
        db (Session | None): Active SQLAlchemy session, used for both memory
                          lookups and logging. Logging is skipped entirely if None.

    Returns:
        list[dict]: The original list of bubbles, mutated to include a new
                    'translation' field containing the localized Polish text.
    """

    if not bubbles:
        return bubbles

    provider = get_provider()
    run_id = str(uuid.uuid4())

    memory_enabled = project_id is not None and db is not None
    logging_enabled = memory_enabled and page_id is not None

    # 1. Look up correction-memory matches up front, before touching the LLM.
    #    bubble_id -> (Correction, similarity_score). Skipped entirely when
    #    no DB context was passed in (see module docstring, point 5).
    matches: dict[str, tuple] = {}
    if memory_enabled:
        for b in bubbles:
            text = b.get("text", "").strip()
            if not text:
                continue
            match = find_best_match(text, project_id, db)
            if match is not None:
                matches[b["bubble_id"]] = match

    # 2. Build the main payload (hints included where matched) and translate.
    #    This is the result that gets shown to the user.
    texts_payload = [
        {
            "id": b["bubble_id"],
            "text": b["text"],
            **({"hint": matches[b["bubble_id"]][0].final_translation} if b["bubble_id"] in matches else {}),
        }
        for b in bubbles
        if b.get("text", "").strip()
    ]

    if not texts_payload:
        for b in bubbles:
            b["translation"] = ""
        return bubbles

    shown_translations = provider.translate(texts_payload)

    # 3. For matched bubbles only, run a second hint-free pass purely for A/B
    #    logging (the thesis' zero-shot vs. memory-injected comparison data).
    zero_shot_translations = {}
    if MEMORY_AB_TEST_LOGGING and logging_enabled and matches:
        counterfactual_payload = [
            {"id": bid, "text": next(b["text"] for b in bubbles if b["bubble_id"] == bid)}
            for bid in matches
        ]
        zero_shot_translations = provider.translate(counterfactual_payload)

    # 4. Apply the shown translation to every bubble (always - regardless of
    #    whether logging is enabled) and build the log rows (only if enabled).
    log_rows = []
    for b in bubbles:
        bubble_id = b["bubble_id"]
        translation = shown_translations.get(bubble_id, "")
        b["translation"] = translation

        if not logging_enabled:
            continue

        if bubble_id in matches:
            correction, score = matches[bubble_id]

            log_rows.append(TranslationLog(
                page_id=page_id, bubble_id=bubble_id, variant="memory_injected",
                source_text=b["text"], output_text=translation,
                matched_correction_id=correction.id, similarity_score=score,
                model_used=ACTIVE_MODEL_NAME, run_id=run_id,
            ))

            if bubble_id in zero_shot_translations:
                log_rows.append(TranslationLog(
                    page_id=page_id, bubble_id=bubble_id, variant="zero_shot",
                    source_text=b["text"], output_text=zero_shot_translations[bubble_id],
                    matched_correction_id=correction.id, similarity_score=score,
                    model_used=ACTIVE_MODEL_NAME, run_id=run_id,
                ))
        else:
            log_rows.append(TranslationLog(
                page_id=page_id, bubble_id=bubble_id, variant="zero_shot",
                source_text=b["text"], output_text=translation,
                matched_correction_id=None, similarity_score=None,
                model_used=ACTIVE_MODEL_NAME, run_id=run_id,
            ))

    if logging_enabled and log_rows:
        db.add_all(log_rows)
        db.commit()

    return bubbles



