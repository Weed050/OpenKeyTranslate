
# backend/services/providers/base.py

"""
Abstract interface all translation backends must implement.

Keeping this interface minimal (one method, one input/output shape) is what
lets translation_service.py stay provider-agnostic: it only ever calls
provider.translate(texts_payload), regardless of which LLM sits behind it.
Adding a new backend means writing one new class here that implements
this method - nothing upstream changes.
"""

from abc import ABC, abstractmethod


class TranslationProvider(ABC):
    def __init__(self):
        # Set by translate() to the label of whichever key from the pool
        # actually served the last request - read by translation_service.py
        # right after each call, for TranslationLog.key_label.
        self.current_key_label: str | None = None

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
