
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
"""

import numpy as np
from sqlalchemy.orm import Session

from models.models import Correction
from core.config import MEMORY_SIMILARITY_THRESHOLD, MEMORY_EMBEDDING_MODEL, MEMORY_MIN_WORDS

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


def find_best_match(
    source_text: str,
    project_id: int,
    db: Session,
    threshold: float = MEMORY_SIMILARITY_THRESHOLD,
) -> tuple[Correction, float] | None:
    """
    Search this project's correction history for the closest match to `source_text`.

    Loads every stored correction for the project, compares embeddings via
    cosine similarity (vectors are pre-normalized, so this reduces to a dot
    product), and returns the single best match if it clears `threshold`.

    :param source_text: The English OCR text about to be translated.
    :param project_id: Restricts the search to corrections from the same
        project, avoiding cross-series contamination (see module docstring).
    :param db: Active SQLAlchemy session.
    :param threshold: Minimum cosine similarity required to count as a match.
    :return: (Correction, similarity_score) for the best match, or None if
        nothing in the project's history clears the threshold.
    """
    corrections = db.query(Correction).filter(Correction.project_id == project_id).all()
    if not corrections:
        return None

    query_vector = embed_text(source_text)
    stored_vectors = np.stack([decode_embedding(c.embedding) for c in corrections])

    # Vectors are pre-normalized at encode time, so the dot product IS the cosine similarity.
    similarities = stored_vectors @ query_vector
    best_idx = int(np.argmax(similarities))
    best_score = float(similarities[best_idx])

    if best_score < threshold:
        return None

    return corrections[best_idx], best_score


def save_correction(
    db: Session,
    project_id: int,
    source_text: str,
    ai_translation: str | None,
    final_translation: str,
) -> Correction | None:
    """
    Persist a confirmed user correction and its embedding - subject to two
    quality gates, so the memory only grows from signal, not noise:

    - Delta gate: skip silent acceptances. A save is only worth storing when
      the user's final text actually differs from what the AI proposed -
      if they just clicked through, the AI's phrasing was already fine and
      re-storing it as a "correction" teaches the retrieval step nothing.
    - Junk gate: skip fragments shorter than MEMORY_MIN_WORDS. Single-word
      interjections ("Tak", "Nie", "Aaa!") embed poorly and tend to produce
      spurious high-similarity matches against unrelated short text later.

    Every bubble is still stored as a new row when it passes (corrections
    are never overwritten in place), so the memory grows across the project
    rather than collapsing similar-but-distinct lines into one entry.

    :return: The created Correction, or None if a gate skipped the save.
        Callers should treat None as "acknowledged, not stored" - not an error.
    """
    final_clean = final_translation.strip()
    ai_clean = (ai_translation or "").strip()

    if final_clean == ai_clean:
        return None

    if len(final_clean.split()) < MEMORY_MIN_WORDS:
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
