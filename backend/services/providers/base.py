
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

import threading
import time
from abc import ABC, abstractmethod

from pydantic import BaseModel

from .prompts import SYSTEM_PROMPT


# ---------------------------------------------------------------- the model that really answered

_served = threading.local()


def note_served_model(name: str | None) -> None:
    """
    Called by a client after a successful request with the model id the SERVER reports (Gemini model_version, the
    "model" field of an OpenAI-style reply). Why: a model id the user typed is not always the model that answers -
    Google routes requests for a shut-down model to its successor silently (gemini-2.5-flash -> gemini-3.5-flash-lite
    after 01.12.2026), so the log would name a model that did not translate. Thread-local: the pool reads it right
    after the call in the same thread (page jobs run in worker threads).
    """
    _served.name = (name or "").strip() or None


def take_served_model() -> str | None:
    """The served model noted by the last call in this thread (None = the server did not say), cleared."""
    name = getattr(_served, "name", None)
    _served.name = None
    return name


def same_model(requested: str | None, served: str | None) -> bool:
    """
    Whether the served model is the requested one. Servers may answer with a dated / "models/" prefixed form of the
    same id (gemini-2.5-flash-001, models/gemini-2.5-flash) - that counts as the same; an unknown served model too.
    """
    if not served or not requested:
        return True
    req = requested.removeprefix("models/").lower()
    srv = served.removeprefix("models/").lower()
    if srv == req or req.endswith("/" + srv) or srv.endswith("/" + req):    # OpenRouter "google/..." prefixes
        return True
    rest = srv.removeprefix(req + "-") if srv.startswith(req + "-") else None
    # a snapshot of the same id: -001, -2024-08-06, -preview-05-20, -latest (NOT -lite / -8b = another model)
    return rest is not None and (rest[:1].isdigit() or rest.startswith(("preview", "latest")))


class BubbleTranslation(BaseModel):
    id: str
    translation: str


class TranslationBatch(BaseModel):
    translations: list[BubbleTranslation]


# ---------------------------------------------------------------- errors of one request with one key

class KeyCallError(Exception):
    """Base of the classified failures of one request. `message` is meant for the user (Settings test box)."""


class RateLimited(KeyCallError):
    """
    429 / quota spent: this key cools down for a while, the next key is tried.
    retry_after = seconds the server asked to wait (Gemini sends it), None = unknown; daily = the DAY's quota is spent
    (free Gemini keys: resets at midnight Pacific time) - waiting a minute and retrying only burns time.
    """

    def __init__(self, message: str = "", retry_after: float | None = None, daily: bool = False,
                 limit: int | None = None, metric: str | None = None):
        super().__init__(message)
        self.retry_after = retry_after
        self.daily = daily
        self.limit = limit          # the number the server named for the spent limit (Gemini, Groq), None = not said
        self.metric = metric        # which limit ("GenerateRequestsPerDayPerProjectPerModel-FreeTier", "requests per day")


class KeyRejected(KeyCallError):
    """401 / 403 / unknown model: retrying cannot help - the key is parked for longer, the next key is tried."""


NO_CREDITS = "No credits left"
"""Start of every OutOfCredits message - callers that only see the pool's text (the replay script) look for it."""


class OutOfCredits(KeyRejected):
    """
    402 Payment Required: the PREPAID balance of the account behind the key is spent (Gemini Prepay - the default for
    new paid accounts since 2026 -, OpenRouter, DeepSeek). Retrying does not help, so the pool treats it like a rejected
    key (parked, next key); a class of its own because the user's fix differs (add credits, not copy the key again) and
    the replay script stops on it instead of failing every remaining request. Gemini: EVERY key of that billing
    account gets 402 at once.
    """


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
        # model id the server reported for that call (None = not said) - see note_served_model
        self.current_served_model: str | None = None
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
