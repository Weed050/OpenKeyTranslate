
# backend/services/glossary_service.py

"""
Deterministic term-base for the translation pipeline.

Separate from services/memory_service.py (fuzzy, embedding, whole-bubble,
rarely clears threshold - see logs). Here: fixed English->Polish term
pairs, matched by exact word presence (word-boundary), injected into
EVERY bubble containing that term, regardless of the rest of the sentence.

Terms are added manually or extracted from existing Corrections (see
suggest_glossary_terms) - suggestions never auto-save, per the project's
HITL philosophy (see README "Core idea").
"""

import re
import json
from collections import Counter
from sqlalchemy.orm import Session
from rapidfuzz import fuzz

from models.models import Correction, GlossaryTerm


def load_glossary(project_id: int, db: Session) -> list[GlossaryTerm]:
    """Load once per page, not per bubble."""
    return db.query(GlossaryTerm).filter(GlossaryTerm.project_id == project_id).all()


def match_glossary(source_text: str, terms: list[GlossaryTerm]) -> list[dict]:
    """Return every term whose source_term appears (whole-word, case-insensitive) in the text."""
    matches = []
    for t in terms:
        if re.search(rf"\b{re.escape(t.source_term)}\b", source_text, re.IGNORECASE):
            matches.append({"source_term": t.source_term, "target_term": t.target_term})
    return matches


def _sim(a: str, b: str) -> float:
    return fuzz.ratio(a.lower(), b.lower())


def _pick_form(forms: Counter) -> str:
    """
    Pick the spelling to show/store for a suggestion. Manga lettering is
    ALL-CAPS, so an all-caps form says nothing about the term's real casing:
    prefer mixed/lower-case forms, then the most frequent one, and on a tie
    the all-lowercase one (common nouns are lowercase mid-sentence; proper
    nouns only ever show up capitalized, so they never tie with a lowercase form).
    """
    pool = [f for f in forms if not f.isupper()] or list(forms)
    return max(pool, key=lambda f: (forms[f], f.islower()))


def suggest_glossary_terms(project_id: int, db: Session, min_occurrences: int = 2) -> list[dict]:
    """
    Extract recurring correction patterns from Correction.

    Heuristic for invented/fantasy proper nouns (the "Wyvern" case): for
    each correction, find English source tokens whose spelling loosely
    matches (rapidfuzz) a token in BOTH ai_translation and
    final_translation - i.e. a name that was transliterated, not
    translated. If the matched Polish token differs between
    ai_translation and final_translation - candidate fix. A candidate seen
    in >= min_occurrences different corrections -> suggestion for the user
    to confirm. Never saves itself.

    Refinements over the naive version:
    - Case variants are merged: "WIWERN" / "Wiwern" / "wiwern" -> "WYWERN" /
      "Wywern" / "wywern" is ONE suggestion, not three (see _pick_form for
      which spelling is shown).
    - Terms already in the project's glossary are not suggested again.
    - Inflection-only fixes are skipped: "Maiharka" -> "Maihark" differs only
      by a Polish case ending, and a glossary entry (which forces one fixed
      target form, no inflection) would break sentences where the declined
      form is the correct one.

    Limitation: the [A-Za-z]+ regex assumes no Polish diacritics in the
    term - true for invented/foreign names by definition, but won't catch
    a native Polish word.
    """
    corrections = db.query(Correction).filter(Correction.project_id == project_id).all()
    already_in_glossary = {
        t.source_term.strip().upper()
        for t in db.query(GlossaryTerm).filter(GlossaryTerm.project_id == project_id).all()
    }

    candidates: Counter = Counter()
    ai_forms: dict = {}
    final_forms: dict = {}
    examples: dict = {}

    for c in corrections:
        if not c.ai_translation:
            continue
        src_tokens = re.findall(r"[A-Za-z]+", c.source_text)
        ai_tokens = re.findall(r"[A-Za-z]+", c.ai_translation)
        final_tokens = re.findall(r"[A-Za-z]+", c.final_translation)
        if not ai_tokens or not final_tokens:
            continue

        for src_tok in src_tokens:
            if len(src_tok) < 4:
                continue  # skip short, common words (I, TO, ARE...)
            if src_tok.upper() in already_in_glossary:
                continue  # already handled

            best_ai = max(ai_tokens, key=lambda t: _sim(src_tok, t))
            if _sim(src_tok, best_ai) < 55:
                continue  # doesn't look like a transliteration - real translated word

            best_final = max(final_tokens, key=lambda t: _sim(src_tok, t))
            if _sim(src_tok, best_final) < 55:
                continue

            ai_low, final_low = best_ai.lower(), best_final.lower()
            if ai_low == final_low:
                continue  # user didn't touch this term - nothing to learn

            # Only a case-ending differs ("maiharka" vs "maihark") - a
            # grammar fix, not a term fix.
            if (ai_low.startswith(final_low) or final_low.startswith(ai_low)) and abs(len(ai_low) - len(final_low)) <= 3:
                continue

            key = (src_tok.upper(), ai_low, final_low)
            candidates[key] += 1
            ai_forms.setdefault(key, Counter())[best_ai] += 1
            final_forms.setdefault(key, Counter())[best_final] += 1
            examples.setdefault(key, c.source_text)

    suggestions = [
        {
            "source_term": key[0],
            "old_translation": _pick_form(ai_forms[key]),
            "new_translation": _pick_form(final_forms[key]),
            "occurrences": count,
            "example_source_text": examples[key],
        }
        for key, count in candidates.items()
        if count >= min_occurrences
    ]
    return sorted(suggestions, key=lambda s: -s["occurrences"])


def get_term_usage(term: "GlossaryTerm", project_id: int, db: Session) -> list[dict]:
    """
    Every logged bubble where this glossary term fired, with both the
    zero-shot and memory-injected outputs (when both were logged for that
    bubble) and whatever the user eventually saved for that exact source
    text - mirrors memory_service.get_correction_usage's shape so the two
    usage panels (Memory / Glossary) read the same way.
    """
    from models.models import TranslationLog, Page, Chapter

    logs = (
        db.query(TranslationLog)
        .join(Page, TranslationLog.page_id == Page.id)
        .join(Chapter, Page.chapter_id == Chapter.id)
        .filter(Chapter.project_id == project_id, TranslationLog.glossary_terms_used.isnot(None))
        .order_by(TranslationLog.created_at.desc())
        .all()
    )

    matching = []
    for log in logs:
        try:
            terms = json.loads(log.glossary_terms_used)
        except (TypeError, ValueError):
            continue
        if any(t.get("source_term", "").lower() == term.source_term.lower() for t in terms):
            matching.append(log)

    if not matching:
        return []

    grouped: dict = {}
    for log in matching:
        grouped.setdefault((log.run_id, log.bubble_id), {})[log.variant] = log

    source_texts = {
        (variants.get("zero_shot") or variants.get("memory_injected")).source_text
        for variants in grouped.values()
    }
    corrections = (
        db.query(Correction)
        .filter(Correction.source_text.in_(source_texts))
        .order_by(Correction.created_at.desc())
        .all()
    )
    final_by_source = {}
    for c in corrections:
        final_by_source.setdefault(c.source_text, c.final_translation)

    result = []
    for variants in grouped.values():
        base = variants.get("zero_shot") or variants.get("memory_injected")
        result.append({
            "source_text": base.source_text,
            "page_id": base.page_id,
            "created_at": base.created_at.isoformat() if base.created_at else None,
            "zero_shot_output": variants["zero_shot"].output_text if "zero_shot" in variants else None,
            "memory_injected_output": variants["memory_injected"].output_text if "memory_injected" in variants else None,
            "final_correction": final_by_source.get(base.source_text),
        })
    return sorted(result, key=lambda r: r["created_at"] or "", reverse=True)