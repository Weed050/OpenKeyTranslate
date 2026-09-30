
# backend/services/memory_service.py

"""
Correction Memory Service
__________________________

Implements the project's core engineering-thesis contribution: a lightweight,
local retrieval mechanism over previously confirmed user corrections.

Each time a bubble is about to be translated, its English source text is
embedded and compared (cosine similarity) against every past correction
stored for the same project. A close-enough match is surfaced as a "hint"
that gets injected into the LLM prompt (see translation_service.py), pushing
the model toward phrasing the user has already approved for similar text.

Design notes:
    - Embeddings are computed locally (sentence-transformers), matching the
      project's privacy-first / local-processing design pillar - only plain
      text ever needs to leave the machine for translation, and that is
      still true here since embedding happens before any API call.
    - Matching is deliberately scoped per-project: reusing a phrase's
      translation across unrelated manga titles risks bleeding one series'
      voice into another. See README "Known limitations" for the open
      question of whether this should ever be relaxed.
    - At the data scale of a thesis project (hundreds to a few thousand
      corrections), a brute-force cosine scan over embeddings loaded into
      memory is simpler to reason about and fast enough - no dedicated
      vector DB needed.
    - Two retrieval lanes, chosen by the length of the text being translated
      (see is_short_phrase):
        * Longer text  -> embedding similarity: up to top_k matches that clear
          the similarity threshold (find_top_matches).
        * Short text (<= MEMORY_SHORT_PHRASE_MAX_WORDS words: interjections,
          sound effects) -> EXACT match on normalized text. Embeddings of one
          or two words are noisy - unrelated short lines can look "similar" -
          while repeated short lines ("SFX: WHIRRRR", "WAIT!") match exactly.
          Short entries are therefore never embedding-searched, and short
          text is never embedding-matched against long entries either.
"""

import re

import numpy as np
from sqlalchemy.orm import Session

from models.models import Correction, TranslationLog
from core.config import (
    MEMORY_SIMILARITY_THRESHOLD, MEMORY_EMBEDDING_MODEL, MEMORY_MIN_WORDS,
    MEMORY_SHORT_PHRASE_MAX_WORDS,
)

_embedder = None


def _get_embedder():
    """
    Lazily initialize and return a singleton SentenceTransformer instance.

    Deferred import/instantiation keeps startup fast for code paths that
    never touch the memory system, and mirrors the singleton pattern already
    used for the translation client in translation_service.py.
    """
    global _embedder
    if _embedder is None:
        from sentence_transformers import SentenceTransformer
        _embedder = SentenceTransformer(MEMORY_EMBEDDING_MODEL)
    return _embedder


def warm_up_embedder():
    """Force-load the embedding model now (see _get_embedder) instead of lazily on the first correction save - called once at app startup so the ~5s first-load cost (model cache validation against huggingface.co) happens in the background before the user ever clicks Save."""
    _get_embedder()


def embed_text(text: str) -> np.ndarray:
    """Compute a single L2-normalized embedding vector for a piece of text."""
    vector = _get_embedder().encode(text, normalize_embeddings=True)
    return np.asarray(vector, dtype=np.float32)


def encode_embedding(vector: np.ndarray) -> bytes:
    """Serialize an embedding vector to raw bytes for DB storage."""
    return np.asarray(vector, dtype=np.float32).tobytes()


def decode_embedding(blob: bytes) -> np.ndarray:
    """Deserialize raw bytes back into an embedding vector."""
    return np.frombuffer(blob, dtype=np.float32)


_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)


def normalize_phrase(text: str) -> str:
    """
    Case-, punctuation- and whitespace-insensitive key for exact matching:
    "WAIT!", "Wait..." and "  wait " all become "wait". Returns "" for
    punctuation-only text ("?!", "...") - callers must treat that as
    "nothing to match on".
    """
    stripped = _PUNCT_RE.sub("", (text or "").lower())
    return " ".join(stripped.split())


def is_short_phrase(text: str) -> bool:
    """
    True when `text` belongs to the exact-match lane: at most
    MEMORY_SHORT_PHRASE_MAX_WORDS whitespace-separated words. Setting that
    to 0 turns the lane off (only empty text counts as "short" then, and
    empty text is never translated or stored).
    """
    return len((text or "").split()) <= MEMORY_SHORT_PHRASE_MAX_WORDS


def _find_exact_phrase_match(source_text: str, corrections: list[Correction]) -> list[tuple[Correction, float]]:
    """
    Exact-match lane: the user's most recent translation of this exact line
    (compared via normalize_phrase). Returns at most one (Correction, 1.0) -
    if the user changed their mind over time, the latest edit wins.
    """
    key = normalize_phrase(source_text)
    if not key:
        return []
    candidates = [c for c in corrections if normalize_phrase(c.source_text) == key]
    if not candidates:
        return []
    latest = max(candidates, key=lambda c: c.id)
    return [(latest, 1.0)]


def find_top_matches(
        source_text: str,
        project_id: int,
        db: Session,
        threshold: float = MEMORY_SIMILARITY_THRESHOLD,
        top_k: int = 3,
) -> list[tuple[Correction, float]]:
    """
    Find this project's past corrections relevant to `source_text`.

    Short text (see is_short_phrase) -> exact-match lane: at most one entry,
    score 1.0. Longer text -> embedding lane: up to `top_k` corrections whose
    cosine similarity clears `threshold`, best first, skipping candidates
    whose final translation duplicates one already picked. Short-phrase
    entries are excluded from the embedding lane (they only match exactly).

    :param source_text: The English OCR text about to be translated.
    :param project_id: Restricts the search to corrections from the same
        project, avoiding cross-series contamination (see module docstring).
    :param db: Active SQLAlchemy session.
    :param threshold: Minimum cosine similarity (embedding lane only).
    :param top_k: Maximum number of matches (embedding lane only).
    :return: List of (Correction, score), best first. Empty if nothing matches.
    """
    corrections = db.query(Correction).filter(Correction.project_id == project_id).all()
    if not corrections:
        return []

    if is_short_phrase(source_text):
        return _find_exact_phrase_match(source_text, corrections)

    candidates = [c for c in corrections if not is_short_phrase(c.source_text)]
    if not candidates:
        return []

    query_vector = embed_text(source_text)
    stored_vectors = np.stack([decode_embedding(c.embedding) for c in candidates])

    # Vectors are pre-normalized at encode time, so the dot product IS the cosine similarity.
    similarities = stored_vectors @ query_vector
    order = np.argsort(similarities)[::-1]

    results: list[tuple[Correction, float]] = []
    seen_finals: set[str] = set()
    for idx in order:
        if len(results) >= top_k:
            break
        score = float(similarities[idx])
        if score < threshold:
            break
        correction = candidates[idx]
        final_key = correction.final_translation.strip().lower()
        if final_key in seen_finals:
            continue
        seen_finals.add(final_key)
        results.append((correction, score))
    return results


def find_best_match(
        source_text: str,
        project_id: int,
        db: Session,
        threshold: float = MEMORY_SIMILARITY_THRESHOLD,
) -> tuple[Correction, float] | None:
    """Single-match convenience wrapper around find_top_matches() (top_k=1)."""
    matches = find_top_matches(source_text, project_id, db, threshold=threshold, top_k=1)
    return matches[0] if matches else None


def save_correction(
        db: Session,
        project_id: int,
        source_text: str,
        ai_translation: str | None,
        final_translation: str,
) -> Correction | None:
    """
    Persist a confirmed user correction and its embedding - subject to a few
    quality gates, so the memory only grows from signal, not noise:

    - Delta gate: skip silent acceptances. A save is only worth storing when
      the user's final text actually differs from what the AI proposed -
      if they just clicked through, the AI's phrasing was already fine and
      re-storing it as a "correction" teaches the retrieval step nothing.
    - Junk gate (longer sources only): skip fragments whose final translation
      is shorter than MEMORY_MIN_WORDS.
    - Short-phrase lane: sources of <= MEMORY_SHORT_PHRASE_MAX_WORDS words
      (interjections, sound effects) are exempt from the junk gate, because
      they are retrieved by EXACT match (see find_top_matches), not by
      embeddings - the noise the junk gate guarded against doesn't apply.
      Punctuation-only sources ("?!", "...") have nothing to match on and
      are skipped.
    - Duplicate gate: skip re-saving what the latest stored correction for
      the same source already says.

    Every bubble is still stored as a new row when it passes (corrections
    are never overwritten in place), so the memory grows across the project
    rather than collapsing similar-but-distinct lines into one entry.

    :return: The created Correction, or None if a gate skipped the save.
        Callers should treat None as "acknowledged, not stored" - not an error.
    """
    final_clean = final_translation.strip()
    ai_clean = (ai_translation or "").strip()

    if final_clean.lower() == ai_clean.lower():
        return None

    short_lane = is_short_phrase(source_text)
    phrase_key = normalize_phrase(source_text)

    if short_lane:
        if not phrase_key:
            return None
    elif len(final_clean.split()) < MEMORY_MIN_WORDS:
        return None

    # Duplicate gate: skip if the most recent stored correction for this
    # source already has this exact final_translation (case-insensitive -
    # the provider sometimes returns lowercase, sometimes UPPERCASE, for
    # otherwise identical text). Short phrases compare by normalized text,
    # so "WAIT!" and "WAIT..." count as the same source. Repeated "Next"
    # clicks / accidental double-submits shouldn't clone rows.
    if short_lane:
        history = (
            db.query(Correction)
            .filter(Correction.project_id == project_id)
            .order_by(Correction.id.desc())
            .all()
        )
        last = next((c for c in history if normalize_phrase(c.source_text) == phrase_key), None)
    else:
        last = (
            db.query(Correction)
            .filter(Correction.project_id == project_id, Correction.source_text == source_text)
            .order_by(Correction.created_at.desc())
            .first()
        )
    if last is not None and last.final_translation.strip().lower() == final_clean.lower():
        return None

    correction = Correction(
        project_id=project_id,
        source_text=source_text,
        ai_translation=ai_translation,
        final_translation=final_translation,
        embedding=encode_embedding(embed_text(source_text)),
    )
    db.add(correction)
    db.commit()
    db.refresh(correction)
    return correction


def delete_correction(db: Session, correction_id: int) -> bool:
    """
    Permanently remove a correction from memory (the "trash" action in the
    TM analytics panel, for a hint that's gone stale or was a mistake).

    :return: True if a row was deleted, False if no such id existed.
    """
    correction = db.query(Correction).filter(Correction.id == correction_id).first()
    if correction is None:
        return False
    db.delete(correction)
    db.commit()
    return True


def get_correction_usage(correction_id: int, db: Session) -> list[dict]:
    """
    Full usage trail for a correction: every bubble where it was injected
    as a hint, alongside the zero-shot counterfactual for that same bubble
    (when AB-logging caught one) and whatever the user eventually saved
    for that exact source text - so the memory browser can show not just
    "it fired" but "did it help, and did the user still have to fix it
    afterward" (see memory.js renderUsage).

    Note: TranslationLog stores only the best (top-1) match per bubble, so
    a correction that was sent as hint #2 or #3 still gets reuse_count
    bumped but does not appear in this trail.
    """
    injected_logs = (
        db.query(TranslationLog)
        .filter(TranslationLog.matched_correction_id == correction_id,
                TranslationLog.variant == "memory_injected")
        .order_by(TranslationLog.created_at.desc())
        .all()
    )
    if not injected_logs:
        return []

    run_ids = {log.run_id for log in injected_logs}
    zero_shot_logs = (
        db.query(TranslationLog)
        .filter(TranslationLog.variant == "zero_shot", TranslationLog.run_id.in_(run_ids))
        .all()
    )
    zero_shot_by_pair = {(log.run_id, log.bubble_id): log.output_text for log in zero_shot_logs}

    source_texts = {log.source_text for log in injected_logs}
    corrections = (
        db.query(Correction)
        .filter(Correction.source_text.in_(source_texts))
        .order_by(Correction.created_at.desc())
        .all()
    )
    final_by_source = {}
    for c in corrections:
        final_by_source.setdefault(c.source_text, c.final_translation)

    return [
        {
            "source_text": log.source_text,
            "similarity_score": log.similarity_score,
            "page_id": log.page_id,
            "created_at": log.created_at.isoformat() if log.created_at else None,
            "zero_shot_output": zero_shot_by_pair.get((log.run_id, log.bubble_id)),
            "memory_injected_output": log.output_text,
            "final_correction": final_by_source.get(log.source_text),
        }
        for log in injected_logs
    ]