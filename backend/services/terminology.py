
# backend/services/terminology.py

"""
Terminology enforcement and extraction around the glossary (services/glossary_service.py).

Based on Moslem 2024 ("Language Modelling Approaches to Adaptive Machine Translation"):
  - glossary terms in the prompt are the most reliable way to fix terminology (better than hoping fuzzy matches
    carry it) - done in translation_service via the per-bubble "glossary" field;
  - terminology-constrained AUTOMATIC POST-EDITING: after translation, check which terms the output is still
    missing and ask the LLM to insert ONLY those, keeping the rest of the draft (in the thesis' WMT23 work this
    nearly doubled term usage) - postedit_missing_terms();
  - LLM-based bilingual TERM EXTRACTION from the translator's corrections (95-97% of extracted terms were judged
    correct) - extract_terms_llm(), feeding the glossary suggestions next to the cheap heuristic.

Polish inflects nouns, so "is the term used?" cannot be an exact string test: term_present() accepts any
inflected form sharing the term's stem ("WYWERNA" -> WYWERNY, WYWERNAMI, WYWERNIE...). The glossary target is
treated as a base form the model inflects - the prompt says so - not as a string to paste verbatim.
"""

import re

from pydantic import BaseModel
from sqlalchemy.orm import Session

from models.models import Correction, GlossaryTerm

_TOKEN_RE = re.compile(r"[^\W\d_]+(?:[-'][^\W\d_]+)*", re.UNICODE)
_VOWELS = "aeiouyąęó"
_MAX_ENDING = 4      # longest Polish inflection ending we tolerate after the stem ("-ami", "-owie", "-ach")


def _tokens(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN_RE.findall(text or "")]


def _stem(word: str) -> str:
    """
    Crude Polish stem: the word without its final vowel(s), at least 3 letters. Consonant alternations
    ("flota" -> "flocie") are not covered - such a form is reported missing and gets one harmless post-edit pass.
    """
    w = word.lower()
    stem = w.rstrip(_VOWELS)
    if len(stem) < 3:
        stem = w[:3] if len(w) > 3 else w
    return stem


def term_present(target_term: str, translation: str, source_term: str = "") -> bool:
    """
    True when every content word (3+ letters) of `target_term` occurs in `translation` in some inflected form:
    a token that starts with the word's stem and adds at most _MAX_ENDING letters. A token identical to the
    untranslated English source word does not count (QUA-TOYNE left as-is when the glossary says QUA-TOYN).
    """
    words = [w for w in _tokens(target_term) if len(w) >= 3] or _tokens(target_term)
    if not words:
        return True
    out_tokens = _tokens(translation)
    src_tokens = set(_tokens(source_term))
    targets = set(words)
    for w in words:
        stem = _stem(w)
        ok = False
        for tok in out_tokens:
            if tok in src_tokens and tok not in targets:
                continue
            if tok.startswith(stem) and len(tok) - len(stem) <= _MAX_ENDING:
                ok = True
                break
        if not ok:
            return False
    return True


def missing_terms(glossary: list[dict], translation: str) -> list[dict]:
    """The glossary pairs (as injected for this bubble) whose Polish term the translation does not contain."""
    if not translation or not translation.strip():
        return []
    return [g for g in glossary if not term_present(g["target_term"], translation, g["source_term"])]


def postedit_missing_terms(provider, items: list[dict]) -> dict[str, str]:
    """
    ONE extra provider call for every bubble of the page that missed a glossary term.
    items: [{"id", "text" (English), "draft" (Polish), "missing_terms": [{"source_term","target_term"}]}]
    Returns {id: edited translation}; an id missing from the reply means "keep the draft".
    """
    from services.providers.prompts import POSTEDIT_PROMPT
    if not items:
        return {}
    return provider.translate(items, system_prompt=POSTEDIT_PROMPT)


# ---------------------------------------------------------------------------------------------------------------
# LLM term extraction -> glossary suggestions
# ---------------------------------------------------------------------------------------------------------------

class _ExtractedTerm(BaseModel):
    id: str
    source_term: str
    target_term: str
    old_term: str = ""


class _ExtractedTerms(BaseModel):
    terms: list[_ExtractedTerm]


def extract_terms_llm(project_id: int, db: Session, provider, limit: int = 80) -> list[dict]:
    """
    Ask the LLM which TERM-level changes the user made in their most recent corrections (one request for up to
    `limit` corrections), then count each proposed term over ALL corrections of the project. Returns suggestions
    in the same shape as glossary_service.suggest_glossary_terms (+ "method": "llm"). Never saves anything - the
    user confirms each one, like the heuristic suggestions.
    """
    from services.providers.prompts import TERM_EXTRACTION_PROMPT

    all_corrections = (
        db.query(Correction)
        .filter(Correction.project_id == project_id)
        .order_by(Correction.id.desc())
        .all()
    )
    edits = [c for c in all_corrections
             if (c.kind or "corrected") == "corrected" and c.ai_translation
             and c.ai_translation.strip().lower() != c.final_translation.strip().lower()][:limit]
    if not edits:
        return []

    payload = [{"id": str(c.id), "en": c.source_text, "ai": c.ai_translation, "final": c.final_translation}
               for c in edits]
    result = provider.request_json(TERM_EXTRACTION_PROMPT, payload, _ExtractedTerms)

    in_glossary = {t.source_term.strip().upper()
                   for t in db.query(GlossaryTerm).filter(GlossaryTerm.project_id == project_id).all()}
    by_id = {str(c.id): c for c in edits}

    merged: dict[str, dict] = {}
    for term in result.terms:
        src = term.source_term.strip()
        tgt = term.target_term.strip()
        origin = by_id.get(term.id)
        if not src or not tgt or src.upper() in in_glossary or origin is None:
            continue
        if src.lower() not in origin.source_text.lower():
            continue   # hallucinated / reworded source term - would never match in the glossary regex anyway
        key = src.upper()
        if key in merged:
            continue
        # how many corrections contain the English term and the Polish target (any inflection) in the final text
        occurrences = sum(
            1 for c in all_corrections
            if src.lower() in c.source_text.lower() and term_present(tgt, c.final_translation, src)
        )
        merged[key] = {
            "source_term": src,
            "old_translation": term.old_term.strip(),
            "new_translation": tgt,
            "occurrences": max(1, occurrences),
            "example_source_text": origin.source_text,
            "method": "llm",
        }
    return sorted(merged.values(), key=lambda s: -s["occurrences"])
