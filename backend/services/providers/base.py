
# backend/services/providers/base.py

"""
Abstract interface of the translation backend + the shared error vocabulary of the per-provider clients.

Two entry points:
- translate(texts_payload, system_prompt=None, pin=None) -> {id: translation}: the page batch (and, with a different
  system prompt, the terminology post-edit pass - same output shape).
- request_json(system_prompt, payload, schema, pin=None) -> schema instance: any other structured call (glossary term
  extraction).

translation_service.py only ever talks to this interface. Since 2026-11-04 the one implementation is the key pool
(pool.py): ONE ordered list of keys that may mix providers (Groq, Gemini, any OpenAI-compatible server). The provider
modules (groq_provider.py, gemini_provider.py, openai_compat_provider.py) are thin clients - one request with one key -
that translate their library's exceptions into the four classes below, so the rotation / retry rules live in one place
instead of being copied into every provider.

`pin` = "provider/label" of one key: the call uses only that key (the A/B counterfactual must go through the SAME key
and model as the shown translation, otherwise the pair compares two models).
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


# ---------------------------------------------------------------- errors of one request with one key

class KeyCallError(Exception):
    """Base of the classified failures of one request. `message` is meant for the user (Settings test box)."""


class RateLimited(KeyCallError):
    """429 / quota spent: this key cools down for a while, the next key is tried."""


class KeyRejected(KeyCallError):
    """401 / 403 / unknown model: retrying cannot help - the key is parked for longer, the next key is tried."""


class Unreachable(KeyCallError):
    """No connection (DNS, offline, VPN, local server not running): retried briefly, then keys on OTHER hosts are tried."""


class Transient(KeyCallError):
    """5xx / overloaded: the same key is retried a couple of times, then the next key."""


class TranslationProvider(ABC):
    default_system_prompt = SYSTEM_PROMPT

    def __init__(self):
        # Set after every successful call - read by translation_service.py right after each call:
        # label of the key that answered (TranslationLog.key_label), its model (TranslationLog.model_used) and
        # "provider/label" (pin for the A/B counterfactual).
        self.current_key_label: str | None = None
        self.current_model: str | None = None
        self.current_key_id: str | None = None
        # "provider/label" -> time.monotonic() until which the key is skipped. A 429 is usually per-MINUTE; the old
        # permanent "exhausted" set killed translation until restart after the first burst (single-key users!).
        self._cooldown_until: dict[str, float] = {}

    def _is_cooling(self, key_id: str) -> bool:
        return time.monotonic() < self._cooldown_until.get(key_id, 0.0)

    def _cool_down(self, key_id: str, seconds: float = 65.0) -> None:
        self._cooldown_until[key_id] = time.monotonic() + seconds

    def _seconds_until_any_key(self) -> int:
        waits = [u - time.monotonic() for u in self._cooldown_until.values() if u > time.monotonic()]
        return int(min(waits)) + 1 if waits else 0

    @abstractmethod
    def request_json(self, system_prompt: str, payload, schema: type[BaseModel], pin: str | None = None) -> BaseModel:
        """
        One structured call with key rotation / retry. `payload` is JSON-serialised (or sent as-is when a str).
        Raises when every configured key is unavailable (rate-limited, rejected or unset) - the caller reports it.
        """
        raise NotImplementedError

    def translate(self, texts_payload: list[dict], system_prompt: str | None = None,
                  pin: str | None = None) -> dict[str, str]:
        """
        Translate a batch of speech-bubble texts.

        :param texts_payload: List of {"id", "text", optional "examples" / "exact" / "glossary" / "context_only"}
            - see prompts.SYSTEM_PROMPT for what each field means to the model.
        :param system_prompt: None = the translation prompt; the terminology post-edit pass passes its own.
        :param pin: "provider/label" = use only this key (A/B counterfactual).
        :return: Mapping of {id: translation}. A missing id is treated by the caller as an empty-string translation;
            when every key is unavailable this raises, so the caller gets a clear error instead of silent empty output.
        """
        batch = self.request_json(system_prompt or self.default_system_prompt, texts_payload, TranslationBatch, pin=pin)
        return {item.id: item.translation for item in batch.translations}
