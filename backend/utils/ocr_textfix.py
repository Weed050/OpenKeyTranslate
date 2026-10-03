
# backend/utils/ocr_textfix.py

"""
Cheap, deterministic post-OCR cleanup of a bubble's text, run BEFORE translation / memory / glossary matching.

Found in real logs (376 bubbles of one project):
  - 33 bubbles carry line-wrap hyphenation artefacts: "SQUAD- RON", "PO- SITION", "PRINCI- PALTY"
  - the OCR reads U as L in this lettering: LNIT, LNKNOWN, LRGENT, ABOLT, COLNTRY, ALTHENTICITY ...
Both poison the LLM input, the embedding (memory) and the glossary regex (WYVERN SQUAD- RON).

SAFETY (a false positive is worse than a missed fix - the original is always kept in bubble["text_raw"]):
  * STUTTERS are never touched. "TH-THEN", "W-WHAT", "I-I", "S-SORRY" have no space after the hyphen, and
    "TH- THEN" is recognised as a stutter because the second part STARTS WITH the first ("TH" -> "THEN").
    A line wrap splits ONE word into two disjoint pieces ("SQUAD" + "RON"); a stutter repeats a prefix.
  * A wrap is joined only if the joined word is a real English word (wordfreq). When both halves are normal
    words and the join is only moderately common ("GUEST- HOUSE", "SO- CALLED") the hyphen is kept.
  * Letter confusions (L->U) are applied only to tokens that are NOT words, and only when the corrected
    token IS a common word. Names (glossary terms / `protected`) are never touched.

skip_reason(): bubbles that contain no letters (page numbers "11", "183", "9:55", "?!", "...") are not worth
a translation slot - the pipeline keeps them in the list but neither translates nor inpaints them.
"""

import re

from wordfreq import zipf_frequency

_WRAP = re.compile(r"\b([A-Za-z]{2,})-\s+([A-Za-z]{2,})\b")   # "SQUAD- RON"  (hyphen + SPACE = line wrap)
_TOKEN = re.compile(r"[A-Za-z']+")
_COMMON = 3.0    # zipf >= 3.0  -> clearly a normal word
_WORD = 2.5      # zipf >= 2.5  -> a real (rarer) word
_HAS_LETTER = re.compile(r"[^\W\d_]", re.UNICODE)
_HAS_DIGIT = re.compile(r"\d")


def _zipf(word: str) -> float:
    return zipf_frequency(word.lower(), "en")


def is_stutter(a: str, b: str) -> bool:
    """'TH' + 'THEN', 'W' + 'WHAT', 'BA' + 'BAKA': the second part repeats the first as a prefix."""
    return b.lower().startswith(a.lower())


def dehyphenate(text: str, protected: frozenset = frozenset()) -> str:
    """'SQUAD- RON' -> 'SQUADRON' when the joined word is real; keeps 'TH-THEN', 'TIME-KEEPING', 'QUA-TOYNE'."""
    def join(m: re.Match) -> str:
        a, b = m.group(1), m.group(2)
        if is_stutter(a, b):
            return m.group(0)                      # stutter: leave exactly as OCR gave it
        if f"{a}-{b}".upper() in protected or f"{a}{b}".upper() in protected:
            return f"{a}-{b}"
        joined = a + b
        za, zb, zj = _zipf(a), _zipf(b), _zipf(joined)
        both_words = za >= _COMMON and zb >= _COMMON
        if zj >= _COMMON:
            return joined                          # MOREOVER, FURTHERMORE, TRANSFERRED
        if zj >= _WORD and not both_words:
            return joined                          # TELEPORTATION (halves are fragments)
        if za < _WORD and zb < _WORD and zj >= 1.5:
            return joined                          # both halves are fragments, joined word exists
        return f"{a}-{b}"                          # compound / unknown: only drop the space
    for _ in range(2):                             # twice: "CUR- RENT- LY" needs two passes
        text = _WRAP.sub(join, text)
    return text


def fix_confusions(text: str, protected: frozenset = frozenset(), pairs=(("L", "U"),)) -> str:
    """Fix systematic letter confusions token by token (only non-words -> common words)."""
    def fix(m: re.Match) -> str:
        tok = m.group(0)
        core = tok.strip("'")
        if len(core) < 3 or core.upper() in protected or _zipf(core) >= _WORD:
            return tok
        best, best_z = None, _COMMON
        for wrong, right in pairs:
            for i, ch in enumerate(core.upper()):
                if ch == wrong:
                    cand = core[:i] + (right if core[i].isupper() else right.lower()) + core[i + 1:]
                    z = _zipf(cand)
                    if z > best_z:
                        best, best_z = cand, z
        return tok.replace(core, best) if best else tok
    return _TOKEN.sub(fix, text)


def fix_ocr_text(text: str, protected=()) -> str:
    """Full cleanup. `protected`: names that must never be altered (glossary source terms, character names)."""
    prot = frozenset(p.upper() for p in protected)
    return fix_confusions(dehyphenate(text or "", prot), prot)


def skip_reason(text: str, skip_symbols: bool = True) -> str | None:
    """
    Why this bubble should NOT be translated (and not inpainted), or None.
      "number"  - digits and no letters: page numbers, "11", "183", "9:55", "3/4"
      "symbols" - no letters/digits at all: "?!", "...", "!!"   (skip_symbols=False keeps them translatable)
      "empty"   - nothing left after stripping
    """
    t = (text or "").strip()
    if not t:
        return "empty"
    if _HAS_LETTER.search(t):
        return None
    if _HAS_DIGIT.search(t):
        return "number"
    return "symbols" if skip_symbols else None