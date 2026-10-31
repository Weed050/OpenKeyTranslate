
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
       is configured as active (see services/providers/__init__.py).
    2. Batch Processing: Aggregates all text bubbles from a page into a single
       provider call (the other bubbles are each other's context).
    3. Translation-Memory Injection (adaptive MT by in-context learning, Moslem 2024 ch. 4): before translating,
       each bubble's source is looked up in the project's TM (services/memory_service.py). Two lanes:
         - longer text ("fuzzy" lane): up to MEMORY_TOP_K similar entries injected as "examples" -
           EN -> PL PAIRS with similarity, kind (corrected / approved) and, for corrections, the rejected AI text;
         - short text ("exact" lane: interjections, sound effects): the user's latest
           translation of that EXACT line is injected as "exact".
    4. Terminology: glossary terms found in the source are injected per bubble; afterwards every output is checked
       for each term (inflection-tolerant, services/terminology.py) and bubbles that missed one get ONE extra
       post-editing call that inserts the term and keeps the rest (glossary_postedit).
    5. Case: an ALL-CAPS source gets an ALL-CAPS translation (translation_match_case) - deterministic, in code.
    6. Zero-Shot vs. Memory-Injected Logging (the thesis experiment): for bubbles with a memory match, a
       counterfactual is translated with the IDENTICAL payload minus the memory fields - same glossary, same page
       context (the other bubbles ride along as context_only). Only memory differs, so a difference between the
       two outputs is the memory effect, not a glossary or context effect. memory_ab_sample_rate limits how many
       runs pay for that second call. Both sides are compared on the MODEL output (before post-editing / case
       normalization: TranslationLog.injection_json["model_output"]); output_text is what was shown.
    7. Full provenance per bubble in TranslationLog.injection_json (see _injection_record) and memory_lane.
    8. Optional DB Context: project_id/page_id/db are optional. Without them translation still runs, just without
       memory, glossary or logging (e.g. test_main.py's test_ocr_from_path()).
"""

import json
import random
import uuid
from sqlalchemy.orm import Session

from core.config import get_setting, get_active_model_name   # LIVE values: Settings page applies without a restart
from models.models import Correction, TranslationLog
from services.memory_service import find_top_matches, is_short_phrase, entry_kind, memory_text
from services.glossary_service import load_glossary, match_glossary
from services.terminology import missing_terms, postedit_missing_terms
from services.providers import get_provider


def is_caps_line(text: str) -> bool:
    """>= 90% of the letters are upper case (OCR sometimes reads one 'Is' in an all-caps line)."""
    letters = [c for c in (text or "") if c.isalpha()]
    if len(letters) < 2:
        return False
    upper = sum(1 for c in letters if c.isupper())
    return upper / len(letters) >= 0.9


def match_case(source: str, translation: str) -> str:
    """Upper-case the translation of an ALL-CAPS source line (Python's upper() handles ą->Ą, ż->Ż...)."""
    if translation and is_caps_line(source) and translation != translation.upper():
        return translation.upper()
    return translation


def _example(correction: Correction, score: float) -> dict:
    """One memory example as the model sees it: an EN -> PL pair (+ what the user rejected, for corrections)."""
    en = memory_text(correction.source_text) or correction.source_text
    pl = correction.final_translation
    kind = entry_kind(correction)
    if is_caps_line(en):
        pl = pl.upper()
    ex = {"en": en, "pl": pl, "similarity": round(float(score), 2), "kind": kind}
    rejected = (correction.ai_translation or "").strip()
    if kind == "corrected" and rejected and rejected.lower() != correction.final_translation.strip().lower():
        ex["rejected"] = rejected.upper() if is_caps_line(en) else rejected
    return ex


def _memory_payload_fields(bubble: dict, found: list[tuple] | None) -> dict:
    """
    Memory-related keys for one bubble's provider payload entry:
    "exact" (one verbatim translation) for the short-phrase lane, or
    "examples" (EN -> PL pairs, best first) for the fuzzy lane. Empty dict when the bubble has no memory match.
    """
    if not found:
        return {}
    if is_short_phrase(bubble["text"]):
        exact = found[0][0].final_translation
        return {"exact": exact.upper() if is_caps_line(bubble["text"]) else exact}
    return {"examples": [_example(c, s) for c, s in found]}


def _lane(bubble: dict, found) -> str | None:
    if not found:
        return None
    return "exact" if is_short_phrase(bubble["text"]) else "fuzzy"


def translate_bubbles(
    bubbles: list[dict],
    project_id: int | None = None,
    page_id: int | None = None,
    db: Session | None = None,
    context: list[dict] | None = None,
) -> list[dict]:
    """
    Translate extracted speech bubble texts via the active LLM provider, with translation-memory examples,
    glossary terms, terminology post-editing and A/B logging (see module docstring).

    Params:
        bubbles (list[dict]): A list of bubble dictionaries. Each dictionary must
                              contain at least 'bubble_id' and 'text'.
        project_id (int | None): Scopes memory / glossary lookups to this project. Skipped entirely if None.
        page_id (int | None): Used to tag logged TranslationLog rows with their
                          source page. Required (together with db) for logging.
        db (Session | None): Active SQLAlchemy session, used for lookups and logging.
        context (list[dict] | None): neighbouring bubbles sent as context_only (single-bubble retranslation).

    Returns:
        list[dict]: The original list of bubbles, mutated to include a new
                    'translation' field containing the localized Polish text.
    """
    if not bubbles:
        return bubbles

    # Bubbles flagged skip (page numbers, "?!", "...": see utils/bubble_post.py) and empty ones are never sent to
    # the LLM, never logged and never counted as memory hits.
    for b in bubbles:
        if b.get("skip") or not (b.get("text") or "").strip():
            b["translation"] = ""
    all_bubbles = bubbles
    bubbles = [b for b in all_bubbles if not b.get("skip") and (b.get("text") or "").strip()]
    if not bubbles:
        return all_bubbles

    MEMORY_TOP_K = get_setting("memory_top_k", 3)
    MEMORY_SIMILARITY_THRESHOLD = get_setting("memory_similarity_threshold", 0.67)
    MEMORY_AB_TEST_LOGGING = get_setting("memory_ab_test_logging", True)
    AB_SAMPLE_RATE = float(get_setting("memory_ab_sample_rate", 1.0))
    POSTEDIT = get_setting("glossary_postedit", True)
    MATCH_CASE = get_setting("translation_match_case", True)
    ACTIVE_MODEL_NAME = get_active_model_name()

    provider = get_provider()
    run_id = str(uuid.uuid4())

    memory_enabled = project_id is not None and db is not None
    logging_enabled = memory_enabled and page_id is not None

    # 1. Memory + glossary lookups up front, before touching the LLM.
    #    bubble_id -> [(Correction, cosine), ...] best first.
    matches: dict[str, list[tuple]] = {}
    glossary_by_bubble: dict[str, list[dict]] = {}
    memory_failed = False
    if memory_enabled:
        glossary_terms = load_glossary(project_id, db)
        tm_entries = db.query(Correction).filter(Correction.project_id == project_id).all()
        for b in bubbles:
            text = b["text"].strip()
            try:
                top = find_top_matches(text, project_id, db, top_k=MEMORY_TOP_K, corrections=tm_entries)
            except Exception as e:
                # embedder failed to load (offline first run, broken cache...): translate WITHOUT memory hints instead
                # of failing the whole page. Reported once per page, health banner shows the cause.
                if not memory_failed:
                    print(f"[TRANSLATION] memory lookup failed, continuing without hints: {type(e).__name__}: {e}")
                memory_failed = True
                top = []
            if top:
                matches[b["bubble_id"]] = top
            g = match_glossary(text, glossary_terms)
            if g:
                glossary_by_bubble[b["bubble_id"]] = g

    memory_fields = {b["bubble_id"]: _memory_payload_fields(b, matches.get(b["bubble_id"])) for b in bubbles}

    def glossary_field(bid: str) -> dict:
        return {"glossary": glossary_by_bubble[bid]} if bid in glossary_by_bubble else {}

    context_items = [
        {"id": c["bubble_id"], "text": c["text"], "context_only": True}
        for c in (context or []) if (c.get("text") or "").strip() and not c.get("skip")
    ]

    # 2. Main payload (memory + glossary where matched). This is the result that gets shown to the user.
    texts_payload = context_items + [
        {"id": b["bubble_id"], "text": b["text"], **memory_fields[b["bubble_id"]], **glossary_field(b["bubble_id"])}
        for b in bubbles
    ]
    model_output = provider.translate(texts_payload)
    shown_key_label = provider.current_key_label
    shown_model = provider.current_model or ACTIVE_MODEL_NAME     # a key can have its own model (Settings)

    # 3. Terminology check + constrained post-edit (one extra call, only for bubbles that missed a glossary term).
    terms_info: dict[str, dict] = {}
    final_text: dict[str, str] = {}
    to_fix = []
    for b in bubbles:
        bid = b["bubble_id"]
        draft = model_output.get(bid, "")
        final_text[bid] = draft
        if bid not in glossary_by_bubble:
            continue
        missing = missing_terms(glossary_by_bubble[bid], draft)
        terms_info[bid] = {"checked": [g["target_term"] for g in glossary_by_bubble[bid]],
                           "missing": [g["target_term"] for g in missing]}
        if missing and POSTEDIT:
            to_fix.append({"id": bid, "text": b["text"], "draft": draft, "missing_terms": missing})
    if to_fix:
        try:
            edited = postedit_missing_terms(provider, to_fix)
        except Exception as e:
            edited = {}
            print(f"[TRANSLATION] terminology post-edit failed, drafts kept: {type(e).__name__}: {e}")
            for item in to_fix:
                terms_info[item["id"]]["postedit_error"] = f"{type(e).__name__}: {e}"
        for item in to_fix:
            bid = item["id"]
            new = (edited.get(bid) or "").strip()
            info = terms_info[bid]
            if not new:
                continue
            still = missing_terms(glossary_by_bubble[bid], new)
            info["postedit"] = {"draft": item["draft"], "result": new,
                                "still_missing": [g["target_term"] for g in still]}
            # keep the edit only if it actually fixed something (never trade a fluent draft for a worse miss)
            if len(still) < len(item["missing_terms"]):
                final_text[bid] = new
                info["postedit"]["applied"] = True
            else:
                info["postedit"]["applied"] = False

    # 4. Counterfactual for the A/B experiment: identical payload, memory fields removed, same glossary and context.
    zero_shot_raw: dict[str, str] = {}
    zero_shot_key_label = None
    zero_shot_model = None
    ab_sampled = MEMORY_AB_TEST_LOGGING and logging_enabled and bool(matches) and random.random() < AB_SAMPLE_RATE
    if ab_sampled:
        counterfactual_payload = context_items + [
            ({"id": b["bubble_id"], "text": b["text"], **glossary_field(b["bubble_id"])}
             if b["bubble_id"] in matches else
             {"id": b["bubble_id"], "text": b["text"], "context_only": True})
            for b in bubbles
        ]
        try:
            zero_shot_raw = provider.translate(counterfactual_payload)
            zero_shot_key_label = provider.current_key_label
            zero_shot_model = provider.current_model or ACTIVE_MODEL_NAME   # != shown_model = pair not comparable
        except Exception as e:
            # the shown translation is already paid for - a failed experiment call must not fail the page
            print(f"[TRANSLATION] A/B counterfactual failed, skipped: {type(e).__name__}: {e}")
            zero_shot_raw = {}

    # 5. Apply the shown translation to every bubble and build the log rows (only if enabled).
    log_rows = []
    for b in bubbles:
        bid = b["bubble_id"]
        raw = model_output.get(bid, "")
        translation = final_text.get(bid, "")
        cased = match_case(b["text"], translation) if MATCH_CASE else translation
        b["translation"] = cased

        found = matches.get(bid)
        if found and memory_enabled:
            for correction, _ in found:
                correction.reuse_count = (correction.reuse_count or 0) + 1
                db.add(correction)

        if not logging_enabled:
            continue

        lane = _lane(b, found)
        injection = {
            "lane": lane,
            **memory_fields[bid],
            "glossary": glossary_by_bubble.get(bid, []),
            "batch_size": len(bubbles),
            "context_bubbles": len(context_items),
            "model_output": raw,
            "case_normalized": cased != translation,
        }
        if bid in terms_info:
            injection["terms"] = terms_info[bid]
        if found:
            injection["ab"] = "counterfactual" if bid in zero_shot_raw else ("not_sampled" if not ab_sampled else "failed")
            # log only (never sent to the model): which memory entries these were, in the order of "examples" / the
            # one "exact" entry - the editor's "Used from memory" panel links each one to the Memory page
            injection["correction_ids"] = [c.id for c, _ in found]
        glossary_json = json.dumps(glossary_by_bubble[bid], ensure_ascii=False) if bid in glossary_by_bubble else None
        common = dict(page_id=page_id, bubble_id=bid, source_text=b["text"], model_used=shown_model,
                      run_id=run_id, glossary_terms_used=glossary_json)

        if found:
            best_correction, best_score = found[0]
            # The similarity threshold only applies to the fuzzy lane; exact-match hits have no threshold.
            threshold_used = None if lane == "exact" else MEMORY_SIMILARITY_THRESHOLD
            log_rows.append(TranslationLog(
                **common, variant="memory_injected", output_text=cased,
                matched_correction_id=best_correction.id, similarity_score=best_score, threshold_used=threshold_used,
                key_label=shown_key_label, memory_lane=lane,
                hints_json=json.dumps(memory_fields[bid], ensure_ascii=False),
                injection_json=json.dumps(injection, ensure_ascii=False),
            ))
            if bid in zero_shot_raw:
                zs_raw = zero_shot_raw[bid]
                log_rows.append(TranslationLog(
                    **dict(common, model_used=zero_shot_model or shown_model), variant="zero_shot",
                    output_text=match_case(b["text"], zs_raw) if MATCH_CASE else zs_raw,
                    matched_correction_id=best_correction.id, similarity_score=best_score,
                    threshold_used=threshold_used, key_label=zero_shot_key_label, memory_lane=lane,
                    injection_json=json.dumps({"lane": None, "counterfactual_of": lane,
                                               "glossary": glossary_by_bubble.get(bid, []),
                                               "batch_size": len(bubbles), "context_bubbles": len(context_items),
                                               "model_output": zs_raw}, ensure_ascii=False),
                ))
        else:
            log_rows.append(TranslationLog(
                **common, variant="zero_shot", output_text=cased,
                matched_correction_id=None, similarity_score=None,
                key_label=shown_key_label, memory_lane=None,
                injection_json=json.dumps(injection, ensure_ascii=False),
            ))

    if memory_enabled:
        # Logging / reuse counters are secondary: a failed commit (old DB schema, "database is locked") must NOT
        # throw away translations that were already paid for.
        try:
            if log_rows:
                db.add_all(log_rows)
            db.commit()
        except Exception as e:
            db.rollback()
            print(f"[TRANSLATION] logging FAILED, translations kept: {type(e).__name__}: {e}")

    return all_bubbles
