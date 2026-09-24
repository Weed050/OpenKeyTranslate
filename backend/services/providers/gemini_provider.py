
# backend/services/providers/gemini_provider.py

"""
Gemini API provider implementation with multi-key rotation and transient-error retry.

Two distinct failure modes, handled differently:
- Quota exhausted (google.genai.errors.ClientError, code 429 /
  RESOURCE_EXHAUSTED): this key's quota is spent for now. Mark it
  exhausted and move on to the next key in the pool.
- Transient server error (google.genai.errors.ServerError, HTTP 5xx -
  "model overloaded", momentary outage): usually resolves within seconds.
  Retry the *same* key a couple of times with a short backoff before
  giving up on it - swapping keys wouldn't address a server-side issue.

Any other failure propagates immediately - neither retrying nor rotating
fixes a malformed request or a non-quota, non-5xx API error.

Uses response_schema (a Pydantic model) instead of a hand-described JSON
shape in the prompt. Unlike GroqProvider's "json_object" mode - which only
guarantees *some* valid JSON comes back - response_schema constrains
generation to that exact structure, so response.parsed can be trusted
directly instead of defensively parsing a raw string.
"""

import json
import time
from pydantic import BaseModel
from google import genai
from google.genai import types
from google.genai import errors as genai_errors

from .base import TranslationProvider
from .prompts import SYSTEM_PROMPT

_SERVER_ERROR_RETRIES = 2
_SERVER_ERROR_BACKOFF_SECONDS = 2


class _BubbleTranslation(BaseModel):
    id: str
    translation: str


class _TranslationBatch(BaseModel):
    translations: list[_BubbleTranslation]


class GeminiProvider(TranslationProvider):
    def __init__(self, keys: list[dict], model: str, temperature: float, max_tokens: int):
        super().__init__()
        if not keys or not any(k.get("api_key") for k in keys):
            raise ValueError(
                "No Gemini api_key configured — fill in providers.gemini.keys in settings.json"
            )
        self._keys = keys
        self._model = model
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._exhausted_labels: set[str] = set()

    def _call_key(self, api_key: str, user_message: str):
        """One request attempt with a single key. Raises on any failure."""
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=self._model,
            contents=user_message,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                response_mime_type="application/json",
                response_schema=_TranslationBatch,
                temperature=self._temperature,
                max_output_tokens=self._max_tokens,
            ),
        )
        batch: _TranslationBatch | None = response.parsed
        if batch is None:
            raise RuntimeError(f"Gemini returned no parseable structured output. Raw: {response.text!r}")
        return {item.id: item.translation for item in batch.translations}

    def translate(self, texts_payload: list[dict]) -> dict[str, str]:
        user_message = json.dumps(texts_payload, ensure_ascii=False)

        available = [
            k for k in self._keys
            if k.get("api_key") and k.get("label", "default") not in self._exhausted_labels
        ]
        if not available:
            raise RuntimeError(
                "No Gemini keys available — all configured keys are rate-limited or unset."
            )

        for key_entry in available:
            label = key_entry.get("label", "default")

            for attempt in range(_SERVER_ERROR_RETRIES + 1):
                try:
                    result = self._call_key(key_entry["api_key"], user_message)
                    self.current_key_label = label
                    return result

                except genai_errors.ClientError as e:
                    if getattr(e, "code", None) == 429:
                        print(f"[GEMINI] key '{label}' rate-limited (429/RESOURCE_EXHAUSTED) — trying next key...")
                        self._exhausted_labels.add(label)
                        break
                    raise  # non-quota client error - don't retry or rotate

                except genai_errors.ServerError:
                    if attempt < _SERVER_ERROR_RETRIES:
                        print(f"[GEMINI] key '{label}' hit a transient server error — "
                              f"retry {attempt + 1}/{_SERVER_ERROR_RETRIES} in {_SERVER_ERROR_BACKOFF_SECONDS}s...")
                        time.sleep(_SERVER_ERROR_BACKOFF_SECONDS)
                        continue
                    print(f"[GEMINI] key '{label}' still failing after {_SERVER_ERROR_RETRIES} retries — trying next key...")
                    break

        raise RuntimeError(
            f"All available Gemini keys are rate-limited or failing (tried: "
            f"{[k.get('label', 'default') for k in available]})."
        )
