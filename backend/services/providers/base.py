
# backend/services/providers/base.py

"""
Abstract interface all translation backends must implement.

Two entry points:
- translate(texts_payload, system_prompt=None) -> {id: translation}: the page batch (and, with a different system
  prompt, the terminology post-edit pass - same output shape).
- request_json(system_prompt, payload, schema) -> schema instance: any other structured call (glossary term
  extraction). Key rotation / retry lives here in each provider, so both paths share it.

translation_service.py only ever talks to this interface, regardless of which LLM sits behind it.
Adding a new backend means writing one new class that implements request_json - nothing upstream changes.
"""

import time
from abc import ABC, abstractmethod

from pydantic import BaseModel

from .prompts import SYSTEM_PROMPT


class BubbleTranslation(BaseModel):
    id: str
    translation: str


class TranslationBatch(BaseModel):
    translations: list[BubbleTranslation]


class TranslationProvider(ABC):
    default_system_prompt = SYSTEM_PROMPT

    def __init__(self):
        # Set by translate() to the label of whichever key from the pool
        # actually served the last request - read by translation_service.py
        # right after each call, for TranslationLog.key_label.
        self.current_key_label: str | None = None
        # Model that served the last request: a key may carry its own "model" (Settings: one row per key), so the
        # provider-level model is only the default. Logged per TranslationLog row (A/B pairs must use one model).
        self.current_model: str | None = None
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
    def request_json(self, system_prompt: str, payload, schema: type[BaseModel]) -> BaseModel:
        """
        One structured call with key rotation / retry. `payload` is JSON-serialised (or sent as-is when a str).
        Raises when every configured key is unavailable (rate-limited or unset) - the caller reports it.
        """
        raise NotImplementedError

    @abstractmethod
    def translate(self, texts_payload: list[dict], system_prompt: str | None = None) -> dict[str, str]:
        """
        Translate a batch of speech-bubble texts.

        :param texts_payload: List of {"id", "text", optional "examples" / "exact" / "glossary" / "context_only"}
            - see prompts.SYSTEM_PROMPT for what each field means to the model.
        :param system_prompt: None = the translation prompt; the terminology post-edit pass passes its own.
        :return: Mapping of {id: translation}. A missing id is treated by the
            caller as an empty-string translation, so failures should simply
            omit the id rather than raise - except when every configured key
            is unavailable (rate-limited or unset), which should raise, so
            the caller gets a clear error instead of silent empty output.
        """
        raise NotImplementedError
