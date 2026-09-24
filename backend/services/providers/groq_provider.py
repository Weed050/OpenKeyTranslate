
# backend/services/providers/groq_provider.py

"""
Groq API provider implementation with multi-key rotation and transient-error retry.

Two distinct failure modes, handled differently:
- Rate limit (groq.RateLimitError, HTTP 429): this key's quota is spent for
  now. Mark it exhausted and move on to the next key in the pool - retrying
  the same key won't help.
- Transient server error (groq.InternalServerError, HTTP 5xx - "model
  overloaded", momentary outage, etc.): usually resolves within seconds.
  Retry the *same* key a couple of times with a short backoff before
  giving up on it - swapping keys wouldn't address a server-side issue.

Any other failure (bad request, malformed JSON reply, auth error)
propagates immediately - neither retrying nor rotating fixes those.

Uses "json_object" response mode - this guarantees syntactically valid
JSON, but not the exact key names/shape, hence the defensive .get() calls
below (contrast with GeminiProvider, which enforces a real schema).
"""

import json
import time
from groq import Groq, RateLimitError, InternalServerError

from .base import TranslationProvider
from .prompts import SYSTEM_PROMPT

_SERVER_ERROR_RETRIES = 2
_SERVER_ERROR_BACKOFF_SECONDS = 2


class GroqProvider(TranslationProvider):
    def __init__(self, keys: list[dict], model: str, temperature: float, max_tokens: int):
        super().__init__()
        if not keys or not any(k.get("api_key") for k in keys):
            raise ValueError(
                "No Groq api_key configured — fill in providers.groq.keys in settings.json"
            )
        self._keys = keys
        self._model = model
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._exhausted_labels: set[str] = set()

    def _call_key(self, api_key: str, user_message: str):
        """One request attempt with a single key. Raises on any failure."""
        client = Groq(api_key=api_key)
        response = client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
            response_format={"type": "json_object"},
            temperature=self._temperature,
            max_tokens=self._max_tokens,
        )
        raw = response.choices[0].message.content.strip()
        parsed_data = json.loads(raw)
        translations_list = parsed_data.get("translations", [])
        return {t.get("id"): t.get("translation", "") for t in translations_list if "id" in t}

    def translate(self, texts_payload: list[dict]) -> dict[str, str]:
        user_message = json.dumps(texts_payload, ensure_ascii=False)

        available = [
            k for k in self._keys
            if k.get("api_key") and k.get("label", "default") not in self._exhausted_labels
        ]
        if not available:
            raise RuntimeError(
                "No Groq keys available — all configured keys are rate-limited or unset."
            )

        for key_entry in available:
            label = key_entry.get("label", "default")

            for attempt in range(_SERVER_ERROR_RETRIES + 1):
                try:
                    result = self._call_key(key_entry["api_key"], user_message)
                    self.current_key_label = label
                    return result

                except RateLimitError:
                    print(f"[GROQ] key '{label}' rate-limited (429) — trying next key in pool...")
                    self._exhausted_labels.add(label)
                    break  # move to the next key, retrying this one won't help

                except InternalServerError:
                    if attempt < _SERVER_ERROR_RETRIES:
                        print(f"[GROQ] key '{label}' hit a transient server error — "
                              f"retry {attempt + 1}/{_SERVER_ERROR_RETRIES} in {_SERVER_ERROR_BACKOFF_SECONDS}s...")
                        time.sleep(_SERVER_ERROR_BACKOFF_SECONDS)
                        continue
                    print(f"[GROQ] key '{label}' still failing after {_SERVER_ERROR_RETRIES} retries — trying next key...")
                    break

                # Any other exception (bad request, JSON parse failure, auth
                # error) is neither a quota nor a transient problem - let it
                # propagate so the real cause surfaces immediately.

        raise RuntimeError(
            f"All available Groq keys are rate-limited or failing (tried: "
            f"{[k.get('label', 'default') for k in available]})."
        )
