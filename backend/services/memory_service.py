
# backend/services/memory_service.py

"""
Correction Memory Service
__________________________

Implements the project's core engineering-thesis contribution: a lightweight,
local retrieval mechanism over the user's confirmed translations - a per-project
translation memory (TM), used for adaptive MT by in-context learning
(Moslem 2024, "Language Modelling Approaches to Adaptive Machine Translation", ch. 4:
fuzzy matches from a TM injected into the prompt as source -> target pairs).

Each time a bubble is about to be translated, its English source text is
embedded and compared (cosine similarity) against every TM entry stored for the
same project. Close-enough matches are injected into the LLM prompt as
EN -> PL example pairs (see translation_service.py / providers/prompts.py),
pushing the model toward phrasing the user has already approved for similar text.

Two kinds of entries (Correction.kind):
    - "corrected": the user changed the AI proposal - the strongest signal. The rejected AI version is kept
      (ai_translation) and shown to the model as "rejected", so it also learns what NOT to write.
    - "approved":  the user explicitly approved the AI proposal unchanged ("Approve to memory" button, only with
      memory_store_approved on - off by default). A plain TM entry: it keeps character
      voice / recurring lines consistent and makes the memory fire far more often.
    Corrected entries win ties at retrieval.

Design notes:
    - Embeddings are computed locally (sentence-transformers), matching the
      project's privacy-first / local-processing design pillar.
    - Matching is deliberately scoped per-project: reusing a phrase's
      translation across unrelated manga titles risks bleeding one series'
      voice into another.
    - At the data scale of a thesis project (hundreds to a few thousand
      entries), a brute-force cosine scan over embeddings loaded into
      memory is simpler to reason about and fast enough - no vector DB needed.
    - Source text is normalized before embedding / exact matching (utils/ocr_textfix: "SQUAD- RON" ->
      "SQUADRON"), so entries saved before the OCR cleanup existed still match cleaned text. Entries embedded
      from un-normalized text can be refreshed with reembed_project() (POST /corrections/reembed/{project}).
    - Two retrieval lanes, chosen by the length of the text being translated
      (see is_short_phrase):
        * Longer text  -> "fuzzy" lane: up to top_k entries that clear BOTH the cosine threshold and a
          lexical gate (content-word overlap, memory_lexical_min) - unless the cosine alone is
          very high (memory_strong_similarity). all-MiniLM-L6-v2 scores "same topic, different line" around
          0.7; such matches are noise as examples (Moslem 2024: low-similarity fuzzy matches add little).
        * Short text (<= MEMORY_SHORT_PHRASE_MAX_WORDS words: interjections,
          sound effects) -> "exact" lane: EXACT match on normalized text.
          Short entries are never embedding-searched, and short text is never
          embedding-matched against long entries either.
"""

import re
from datetime import datetime, timezone
from functools import lru_cache

import numpy as np
from sqlalchemy.orm import Session

from models.models import Correction, TranslationLog
from core.config import MEMORY_EMBEDDING_MODEL, get_setting   # tuning knobs are read LIVE via get_setting (no restart)

import threading

_embedder = None
_embedder_lock = threading.Lock()

# Observable by GET /system/health: "idle" | "loading" | "ready" | "error"
EMBEDDER_STATE: dict = {"state": "idle", "error": None, "seconds": None}

# Ranking bonus for a user edit over a plain approval at (almost) the same similarity.
_CORRECTED_BONUS = 0.02


def _get_embedder():
    """
    Lazily initialize and return a singleton SentenceTransformer instance.

    Deferred import/instantiation keeps startup fast for code paths that
    never touch the memory system, and mirrors the singleton pattern already
    used for the translation client in translation_service.py.
    """
    global _embedder
    if _embedder is None:
        with _embedder_lock:                 # a request arriving mid warm-up waits for it instead of loading a 2nd copy
            if _embedder is None:
                import time
                started = time.monotonic()
                EMBEDDER_STATE.update(state="loading", error=None)
                try:
                    from sentence_transformers import SentenceTransformer
                    _embedder = SentenceTransformer(MEMORY_EMBEDDING_MODEL)
                except Exception as e:
                    EMBEDDER_STATE.update(state="error", error=f"{type(e).__name__}: {e}")
                    raise
                EMBEDDER_STATE.update(state="ready", seconds=round(time.monotonic() - started, 1))
    return _embedder


def warm_up_embedder_async() -> threading.Thread:
    """
    Load the embedding model in a background thread (called from main.py's lifespan). The app starts serving at once;
    the ~5-30 s model load (torch import + huggingface cache check) no longer delays startup, and users who open
    the app only to browse pages never pay for it. If a translation or a correction-save needs the model before
    the thread is done, it simply waits on the same lock. A failed load is recorded in EMBEDDER_STATE (health banner).
    """
    def _run():
        try:
            _get_embedder()
            print(f"[MEMORY] embedder ready in {EMBEDDER_STATE['seconds']}s (background warm-up).")
        except Exception as e:
            print(f"[MEMORY] WARNING embedder failed to load: {type(e).__name__}: {e}")
    t = threading.Thread(target=_run, name="embedder-warmup", daemon=True)
    t.start()
    return t


def warm_up_embedder():
    """Force-load the embedding model now (see _get_embedder) instead of lazily on the first correction save - called once at app startup so the ~5s first-load cost (model cache validation against huggingface.co) happens in the background before the user ever clicks Save."""
    _get_embedder()


@lru_cache(maxsize=8192)
def memory_text(text: str) -> str:
    """
    The form of a source line used for embedding and exact matching: OCR artefacts fixed ("SQUAD- RON" ->
    "SQUADRON", LNIT -> UNIT) and whitespace collapsed. Never stored - the Correction keeps what the user saw.
    """
    text = " ".join((text or "").split())
    if not text:
        return ""
    try:
        from utils.ocr_textfix import fix_ocr_text
        return fix_ocr_text(text)
    except Exception:
        return text   # wordfreq missing / unexpected input: matching on the raw text is still fine


def embed_text(text: str) -> np.ndarray:
    """Compute a single L2-normalized embedding vector for a piece of (already normalized) text."""
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
    return len((text or "").split()) <= get_setting("memory_short_phrase_max_words", 2)


def entry_kind(correction: Correction) -> str:
    """Rows from before Correction.kind existed are user edits (the old code stored nothing else)."""
    return correction.kind or "corrected"


def _latest_per_source(corrections: list[Correction]) -> list[Correction]:
    """
    One entry per normalized source line: the most recent one (the user's latest decision). Keeps a stale
    first translation of a line from competing with the newer one as a second "example" of the same sentence.
    """
    latest: dict[str, Correction] = {}
    for c in corrections:
        key = normalize_phrase(memory_text(c.source_text)) or c.source_text
        if key not in latest or c.id > latest[key].id:
            latest[key] = c
    return list(latest.values())


def _find_exact_phrase_match(source_text: str, corrections: list[Correction]) -> list[tuple[Correction, float]]:
    """
    Exact-match lane: the user's most recent translation of this exact line
    (compared via normalize_phrase). Returns at most one (Correction, 1.0) -
    if the user changed their mind over time, the latest decision wins.
    """
    key = normalize_phrase(memory_text(source_text))
    if not key:
        return []
    candidates = [c for c in corrections if normalize_phrase(memory_text(c.source_text)) == key]
    if not candidates:
        return []
    latest = max(candidates, key=lambda c: c.id)
    return [(latest, 1.0)]


_STOPWORDS = frozenset(
    "a an the of to is are was were be been it its this that these those i you he she we they me him her us them "
    "my your his our their in on at for and or but so s m re ll ve d t what who".split()
)


def _content_words(text: str) -> set[str]:
    return {w for w in normalize_phrase(text).split() if w not in _STOPWORDS and len(w) > 1}


def lexical_overlap(a: str, b: str) -> float:
    """
    0-100: Jaccard overlap of CONTENT words (stopwords dropped). Measured on the project's own logs it separates
    useful fuzzy matches ("FLAG OF QUA-TOYNE PRINCIPALITY" ~ "QUA-TOYNE PRINCIPALITY": 67) from same-topic noise
    ("PLEASE USE THE WYVERNS WE PROVIDED..." ~ "ONLY WYVERNS CAN TAKE DOWN WYVERNS...": 8) far better than
    rapidfuzz's token_set_ratio, which scored both around 45-100.
    """
    wa, wb = _content_words(a), _content_words(b)
    if not wa or not wb:
        return 0.0
    return 100.0 * len(wa & wb) / len(wa | wb)


def find_top_matches(
        source_text: str,
        project_id: int,
        db: Session,
        threshold: float | None = None,
        top_k: int | None = None,
        corrections: list[Correction] | None = None,
) -> list[tuple[Correction, float]]:
    """
    Find this project's TM entries relevant to `source_text`.

    Short text (see is_short_phrase) -> exact-match lane: at most one entry,
    score 1.0. Longer text -> fuzzy lane: up to `top_k` entries whose cosine
    similarity clears `threshold` AND whose word overlap clears memory_lexical_min (or whose cosine alone clears
    memory_strong_similarity), best first; corrected entries win near-ties; one entry per source line and no two
    examples with the same final translation.

    :param source_text: The English OCR text about to be translated.
    :param project_id: Restricts the search to entries from the same project.
    :param db: Active SQLAlchemy session.
    :param threshold: Minimum cosine similarity (fuzzy lane only).
    :param top_k: Maximum number of matches (fuzzy lane only).
    :param corrections: Pre-loaded entries of this project (translate_bubbles loads them once per page).
    :return: List of (Correction, cosine score), best first. Empty if nothing matches.
    """
    if threshold is None:
        threshold = get_setting("memory_similarity_threshold", 0.67)
    if top_k is None:
        top_k = get_setting("memory_top_k", 3)
    lexical_min = get_setting("memory_lexical_min", 25) or 0
    strong = get_setting("memory_strong_similarity", 0.90)

    if corrections is None:
        corrections = db.query(Correction).filter(Correction.project_id == project_id).all()
    if not corrections:
        return []

    if is_short_phrase(source_text):
        return _find_exact_phrase_match(source_text, corrections)

    candidates = [c for c in _latest_per_source(corrections) if not is_short_phrase(c.source_text)]
    if not candidates:
        return []

    query = memory_text(source_text)
    query_vector = embed_text(query)
    stored_vectors = np.stack([decode_embedding(c.embedding) for c in candidates])

    # Vectors are pre-normalized at encode time, so the dot product IS the cosine similarity.
    similarities = stored_vectors @ query_vector

    scored = []
    for idx, sim in enumerate(similarities):
        sim = float(sim)
        if sim < threshold:
            continue
        c = candidates[idx]
        if sim < strong and lexical_min and lexical_overlap(query, memory_text(c.source_text)) < lexical_min:
            continue
        rank = sim + (_CORRECTED_BONUS if entry_kind(c) == "corrected" else 0.0)
        scored.append((rank, sim, c))
    scored.sort(key=lambda t: t[0], reverse=True)

    results: list[tuple[Correction, float]] = []
    seen_finals: set[str] = set()
    for _, sim, correction in scored:
        if len(results) >= top_k:
            break
        final_key = correction.final_translation.strip().lower()
        if final_key in seen_finals:
            continue
        seen_finals.add(final_key)
        results.append((correction, sim))
    return results


def find_best_match(
        source_text: str,
        project_id: int,
        db: Session,
        threshold: float | None = None,
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
        approve: bool = False,
) -> Correction | None:
    """
    Persist a confirmed translation and its embedding as a TM entry - subject to a few
    quality gates, so the memory only grows from signal, not noise:

    - Kind: final != AI proposal -> "corrected". final == AI proposal (the user accepted it unchanged) ->
      "approved" only for an explicit approval (approve=True) with memory_store_approved on, otherwise nothing.
      Off by default: approved entries are the model's own text fed back to it as examples, and moving on from a
      bubble used to approve it silently (an accidental "WIWERN" became memory that way).
    - Junk gate (longer sources only): skip fragments whose final translation
      is shorter than MEMORY_MIN_WORDS.
    - Short-phrase lane: sources of <= MEMORY_SHORT_PHRASE_MAX_WORDS words
      (interjections, sound effects) are exempt from the junk gate, because
      they are retrieved by EXACT match (see find_top_matches), not by
      embeddings. Punctuation-only sources ("?!", "...") have nothing to match on and are skipped.
    - Duplicate gate: skip re-saving what the latest stored entry for the same source already says
      (an approval of a line that already has that translation adds nothing).

    Every bubble is still stored as a new row when it passes (entries are never overwritten in place); retrieval
    uses the latest entry per source line.

    :return: The created Correction, or None if a gate skipped the save.
        Callers should treat None as "acknowledged, not stored" - not an error.
    """
    final_clean = final_translation.strip()
    ai_clean = (ai_translation or "").strip()

    kind = "corrected"
    if final_clean.lower() == ai_clean.lower():
        if not (approve and get_setting("memory_store_approved", False)):
            return None
        kind = "approved"

    short_lane = is_short_phrase(source_text)
    phrase_key = normalize_phrase(memory_text(source_text))

    if not final_clean:
        return None   # a cleared textbox must never become a stored (empty) hint
    if short_lane:
        if not phrase_key:
            return None
    elif len(final_clean.split()) < get_setting("memory_min_words", 3):
        return None

    # Duplicate gate: the most recent stored entry for this source (normalized - "WAIT!" == "WAIT...",
    # "SQUAD- RON" == "SQUADRON") already has this exact final_translation (case-insensitive - the provider
    # sometimes returns lowercase, sometimes UPPERCASE). Repeated "Next" clicks / double-submits don't clone rows.
    history = (
        db.query(Correction)
        .filter(Correction.project_id == project_id)
        .order_by(Correction.id.desc())
        .all()
    )
    last = next((c for c in history if normalize_phrase(memory_text(c.source_text)) == phrase_key), None)
    if last is not None and last.final_translation.strip().lower() == final_clean.lower():
        return None

    correction = Correction(
        project_id=project_id,
        source_text=source_text,
        ai_translation=ai_translation,
        final_translation=final_clean,
        kind=kind,
        embedding=encode_embedding(embed_text(memory_text(source_text))),
    )
    db.add(correction)
    db.commit()
    db.refresh(correction)
    return correction


def reembed_project(db: Session, project_id: int) -> int:
    """
    Recompute every entry's embedding from the normalized source (memory_text). Needed once for entries saved
    before the OCR cleanup / normalization existed, or after changing memory_embedding_model. Returns the count.
    """
    rows = db.query(Correction).filter(Correction.project_id == project_id).all()
    for c in rows:
        c.embedding = encode_embedding(embed_text(memory_text(c.source_text)))
    db.commit()
    return len(rows)


def _set_final(correction: Correction, new_final: str) -> None:
    """Change a stored entry's final text, keeping the first-saved version (final_before_edit) for the evaluation."""
    if correction.final_before_edit is None:
        correction.final_before_edit = correction.final_translation
    correction.final_translation = new_final
    correction.edited_at = datetime.now(timezone.utc)
    if (correction.ai_translation or "").strip().lower() != new_final.strip().lower():
        correction.kind = "corrected"     # an edited approval is the user's own wording now


def edit_correction(db: Session, correction_id: int, new_final: str) -> Correction | None:
    """
    Inline edit from the Memory page. The embedding is NOT recomputed - it is built from the English source, which
    does not change. None when there is no such entry.
    """
    correction = db.query(Correction).filter(Correction.id == correction_id).first()
    if correction is None:
        return None
    new_final = new_final.strip()
    if new_final and new_final != correction.final_translation:
        _set_final(correction, new_final)
        db.commit()
        db.refresh(correction)
    return correction


def _same_case(original: str, replacement: str) -> str:
    """Give the replacement the letter case of the text it replaces (memory is mostly ALL CAPS)."""
    if original.isupper():
        return replacement.upper()
    if original.islower():
        return replacement.lower()
    if original[:1].isupper() and original[1:].islower():
        return replacement[:1].upper() + replacement[1:].lower()
    return replacement


def replace_in_memory(db: Session, project_id: int, find: str, replace: str, mode: str = "word",
                      ids: list[int] | None = None, apply: bool = False) -> list[dict]:
    """
    Find & replace in the stored final translations of one project (the Memory page; also offered after a glossary
    term is accepted, so old entries stop contradicting it).

    mode "word":   whole words only, case-insensitive (WIWERN, not WIWERNY).
    mode "prefix": words STARTING with `find` - the term and its Polish inflected forms: find "WIWERN" ->
                   WIWERN, WIWERNY, WIWERNAMI, each keeping its own ending (WYWERN, WYWERNY, WYWERNAMI).
    The replacement takes the letter case of what it replaces. Only `ids` are changed when given (the user unticked
    the rest of the preview). apply=False is a dry run.

    :return: [{"id", "source_text", "before", "after"}] for every entry that changes.
    """
    find = (find or "").strip()
    if len(find) < 2:
        return []
    word = re.escape(find)
    if mode == "prefix":
        pattern = re.compile(rf"(?<!\w){word}(\w*)", re.IGNORECASE)

        def substitute(m):
            rest = m.group(1)
            return _same_case(m.group(0)[:len(m.group(0)) - len(rest)], replace) + rest
    else:
        pattern = re.compile(rf"(?<!\w){word}(?!\w)", re.IGNORECASE)

        def substitute(m):
            return _same_case(m.group(0), replace)

    wanted = set(ids) if ids is not None else None
    changes = []
    rows = db.query(Correction).filter(Correction.project_id == project_id).order_by(Correction.id.desc()).all()
    for c in rows:
        if wanted is not None and c.id not in wanted:
            continue
        after = pattern.sub(substitute, c.final_translation)
        if after == c.final_translation:
            continue
        changes.append({"id": c.id, "source_text": c.source_text, "before": c.final_translation, "after": after})
        if apply:
            _set_final(c, after)
    if apply and changes:
        db.commit()
    return changes


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

    Note: TranslationLog.matched_correction_id stores only the best (top-1) match per bubble; examples #2/#3 are in
    injection_json, but this trail lists top-1 hits only.
    """
    owner = db.query(Correction).filter(Correction.id == correction_id).first()
    if owner is None:
        return []
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
        .filter(Correction.project_id == owner.project_id, Correction.source_text.in_(source_texts))
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
