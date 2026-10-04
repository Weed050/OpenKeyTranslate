
# backend/services/providers/base.py

"""
Abstract interface all translation backends must implement.

Keeping this interface minimal (one method, one input/output shape) is what
lets translation_service.py stay provider-agnostic: it only ever calls
provider.translate(texts_payload), regardless of which LLM sits behind it.
Adding a new backend means writing one new class here that implements
this method - nothing upstream changes.
"""

import time
from abc import ABC, abstractmethod


class TranslationProvider(ABC):
    def __init__(self):
        # Set by translate() to the label of whichever key from the pool
        # actually served the last request - read by translation_service.py
        # right after each call, for TranslationLog.key_label.
        self.current_key_label: str | None = None
        # label -> time.monotonic() until which the key is skipped. A 429 is usually per-MINUTE; the old
        # permanent "exhausted" set killed translation until restart after the first burst (single-key users!).
        self._cooldown_until: dict[str, float] = {}

    def _is_cooling(self, label: str) -> bool:
        return time.monotonic() < self._cooldown_until.get(label, 0.0)

    def _cool_down(self, label: str, seconds: float = 65.0) -> None:
        self._cooldown_until[label] = time.monotonic() + seconds

    def _seconds_until_any_key(self) -> int:
        waits = [u - time.monotonic() for u in self._cooldown_until.values() if u > time.monotonic()]
        return int(min(waits)) + 1 if waits else 0

    @abstractmethod
    def translate(self, texts_payload: list[dict]) -> dict[str, str]:
        """
        Translate a batch of speech-bubble texts.

        :param texts_payload: List of {"id": str, "text": str, "hint": str (optional)}.
        :param texts_payload: "hint", when present, is a translation the user
            already approved for similar text - see services/memory_service.py.
        :return: Mapping of {id: translation}. A missing id is treated by the
            caller as an empty-string translation, so failures should simply
            omit the id rather than raise - except when every configured key
            is unavailable (rate-limited or unset), which should raise, so
            the caller gets a clear error instead of silent empty output.
        """
        raise NotImplementedError
