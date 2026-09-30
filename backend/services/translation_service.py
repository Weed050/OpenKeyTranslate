
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
       services/memory_service.py). Two lanes, depending on text length:
         - longer text: up to MEMORY_TOP_K similar past corrections are
           injected as a per-bubble "hints" list (style examples);
         - short text (interjections, sound effects): the user's latest
           translation of that EXACT line is injected as "exact".
    4. Zero-Shot vs. Memory-Injected Logging: When a hint is used, the same
       bubble is additionally translated a second time without the hint,
       purely for logging (see models.models.TranslationLog). This produces
       the paired data the thesis experiment is evaluated from. Only the
       hinted result is ever shown to the user. TranslationLog keeps only the
       best (top-1) matched correction per bubble.
    5. Optional DB Context: project_id/page_id/db are optional. Pass them
       (from an active session) to enable correction-memory hints and A/B
       logging. Omit them for standalone runs with no DB-backed project -
       e.g. test_main.py's test_ocr_from_path() - translation still runs,
       just without memory or logging.
"""

import json
import uuid
from sqlalchemy.orm import Session

from core.config import MEMORY_AB_TEST_LOGGING, ACTIVE_MODEL_NAME, MEMORY_SIMILARITY_THRESHOLD, MEMORY_TOP_K
from models.models import TranslationLog
from services.memory_service import find_top_matches, is_short_phrase
from services.glossary_service import load_glossary, match_glossary
from services.providers import get_provider


def _memory_payload_fields(bubble: dict, matches: dict) -> dict:
    """
    Memory-related keys for one bubble's provider payload entry:
    "exact" (one verbatim translation) for the short-phrase lane, or
    "hints" (list of style examples, best first) for the embedding lane.
    Empty dict when the bubble has no memory match.
    """
    found = matches.get(bubble["bubble_id"])
    if not found:
        return {}
    if is_short_phrase(bubble["text"]):
        return {"exact": found[0][0].final_translation}
    return {"hints": [correction.final_translation for correction, _ in found]}


def translate_bubbles(
    bubbles: list[dict],
    project_id: int | None = None,
    page_id: int | None = None,
    db: Session | None = None,
) -> list[dict]:
    """
    Translate extracted speech bubble texts via the active LLM provider,
    injecting correction-memory hints where the project has similar past
    corrections.

    Params:
        bubbles (list[dict]): A list of bubble dictionaries. Each dictionary must
                              contain at least 'bubble_id' and 'text'.
        project_id (int | None): Used to scope correction-memory lookups to this
                          project (see services/memory_service.find_top_matches).
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
    #    bubble_id -> [(Correction, similarity_score), ...] best first. Skipped
    #    entirely when no DB context was passed in (see module docstring, point 5).
    matches: dict[str, list[tuple]] = {}
    glossary_by_bubble: dict[str, list[dict]] = {}
    if memory_enabled:
        glossary_terms = load_glossary(project_id, db)
        for b in bubbles:
            text = b.get("text", "").strip()
            if not text:
                continue
            top = find_top_matches(text, project_id, db, top_k=MEMORY_TOP_K)
            if top:
                matches[b["bubble_id"]] = top
            g = match_glossary(text, glossary_terms)
            if g:
                glossary_by_bubble[b["bubble_id"]] = g

    # 2. Build the main payload (hints included where matched) and translate.
    #    This is the result that gets shown to the user.
    texts_payload = [
        {
            "id": b["bubble_id"],
            "text": b["text"],
            **_memory_payload_fields(b, matches),
            **({"glossary": glossary_by_bubble[b["bubble_id"]]} if b["bubble_id"] in glossary_by_bubble else {}),
        }
        for b in bubbles
        if b.get("text", "").strip()
    ]

    if not texts_payload:
        for b in bubbles:
            b["translation"] = ""
        return bubbles

    shown_translations = provider.translate(texts_payload)
    shown_key_label = provider.current_key_label

    # 3. For matched bubbles only, run a second hint-free pass purely for A/B
    #    logging (the thesis' zero-shot vs. memory-injected comparison data).
    zero_shot_translations = {}
    zero_shot_key_label = None
    if MEMORY_AB_TEST_LOGGING and logging_enabled and matches:
        counterfactual_payload = [
            {"id": bid, "text": next(b["text"] for b in bubbles if b["bubble_id"] == bid)}
            for bid in matches
        ]
        zero_shot_translations = provider.translate(counterfactual_payload)
        zero_shot_key_label = provider.current_key_label

    # 4. Apply the shown translation to every bubble (always - regardless of
    #    whether logging is enabled) and build the log rows (only if enabled).
    log_rows = []
    for b in bubbles:
        bubble_id = b["bubble_id"]
        translation = shown_translations.get(bubble_id, "")
        b["translation"] = translation

        if not logging_enabled:
            continue

        glossary_json = json.dumps(glossary_by_bubble[bubble_id]) if bubble_id in glossary_by_bubble else None

        if bubble_id in matches:
            top_matches = matches[bubble_id]
            best_correction, best_score = top_matches[0]
            for correction, _ in top_matches:
                correction.reuse_count = (correction.reuse_count or 0) + 1
                db.add(correction)

            # The similarity threshold only applies to the embedding lane;
            # exact-match (short phrase) hits have no threshold.
            threshold_used = None if is_short_phrase(b["text"]) else MEMORY_SIMILARITY_THRESHOLD

            log_rows.append(TranslationLog(
                page_id=page_id, bubble_id=bubble_id, variant="memory_injected",
                source_text=b["text"], output_text=translation,
                matched_correction_id=best_correction.id, similarity_score=best_score,
                threshold_used=threshold_used,
                model_used=ACTIVE_MODEL_NAME, key_label=shown_key_label, run_id=run_id,
                glossary_terms_used=glossary_json,
            ))

            if bubble_id in zero_shot_translations:
                log_rows.append(TranslationLog(
                    page_id=page_id, bubble_id=bubble_id, variant="zero_shot",
                    source_text=b["text"], output_text=zero_shot_translations[bubble_id],
                    matched_correction_id=best_correction.id, similarity_score=best_score,
                    threshold_used=threshold_used,
                    model_used=ACTIVE_MODEL_NAME, key_label=zero_shot_key_label, run_id=run_id,
                    glossary_terms_used=glossary_json,
                ))
        else:
            log_rows.append(TranslationLog(
                page_id=page_id, bubble_id=bubble_id, variant="zero_shot",
                source_text=b["text"], output_text=translation,
                matched_correction_id=None, similarity_score=None,
                model_used=ACTIVE_MODEL_NAME, key_label=shown_key_label, run_id=run_id,
                glossary_terms_used=glossary_json,
            ))

    if logging_enabled and log_rows:
        db.add_all(log_rows)
        db.commit()

    return bubbles