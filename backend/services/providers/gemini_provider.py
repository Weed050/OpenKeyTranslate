
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

Thinking: Gemini 2.5 models "think" by default and those tokens count against max_output_tokens - a dense page
could end in a truncated, unparseable JSON. gemini_thinking_budget (settings) caps it; 0 = off on Flash.
"""

import json
import time
import httpx
from pydantic import BaseModel
from google import genai
from google.genai import types
from google.genai import errors as genai_errors

from core.config import get_setting
from .base import TranslationProvider, TranslationBatch

_SERVER_ERROR_RETRIES = 2
_SERVER_ERROR_BACKOFF_SECONDS = 2


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

    def _thinking_config(self, model: str):
        """None = leave the model default. Only Gemini 2.5 takes a token budget (3.x uses thinking_level)."""
        budget = get_setting("gemini_thinking_budget", 0)
        if budget is None or budget < 0 or "2.5" not in model:
            return None
        if "pro" in model and budget < 128:
            budget = 128                     # 2.5 Pro cannot switch thinking off
        return types.ThinkingConfig(thinking_budget=int(budget))

    def _call_key(self, api_key: str, system_prompt: str, user_message: str, schema: type[BaseModel],
                  model: str) -> BaseModel:
        """One request attempt with a single key. Raises on any failure."""
        client = genai.Client(api_key=api_key)
        config = dict(
            system_instruction=system_prompt,
            response_mime_type="application/json",
            response_schema=schema,
            temperature=self._temperature,
            max_output_tokens=self._max_tokens,
        )
        thinking = self._thinking_config(model)
        if thinking is not None:
            config["thinking_config"] = thinking
        response = client.models.generate_content(
            model=model,
            contents=user_message,
            config=types.GenerateContentConfig(**config),
        )
        parsed = response.parsed
        if parsed is None:
            finish = ""
            try:
                finish = f" finish_reason={response.candidates[0].finish_reason}"
            except (AttributeError, IndexError, TypeError):
                pass
            raise RuntimeError(f"Gemini returned no parseable structured output{finish}. Raw: {response.text!r}")
        return parsed

    def request_json(self, system_prompt: str, payload, schema: type[BaseModel]) -> BaseModel:
        user_message = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)

        available = [
            k for k in self._keys
            if k.get("api_key") and not self._is_cooling(k.get("label", "default"))
        ]
        if not available:
            raise RuntimeError(
                f"No Gemini keys available — all keys are rate-limited (retry in ~{self._seconds_until_any_key()}s) or unset."
            )

        for key_entry in available:
            label = key_entry.get("label", "default")
            model = key_entry.get("model") or self._model       # per-key model overrides the provider default

            for attempt in range(_SERVER_ERROR_RETRIES + 1):
                try:
                    result = self._call_key(key_entry["api_key"], system_prompt, user_message, schema, model)
                    self.current_key_label = label
                    self.current_model = model
                    return result

                except genai_errors.ClientError as e:
                    if getattr(e, "code", None) == 429:
                        print(f"[GEMINI] key '{label}' rate-limited (429/RESOURCE_EXHAUSTED) — trying next key...")
                        self._cool_down(label)
                        break
                    raise  # non-quota client error - don't retry or rotate

                except (httpx.TransportError, OSError) as e:
                    # DNS failure ("[Errno 11001] getaddrinfo failed"), no internet, VPN/firewall, timeout.
                    # Identical for every key in the pool -> retry briefly, then stop with a clear message
                    # instead of rotating through (and "using up") all keys.
                    if attempt < _SERVER_ERROR_RETRIES:
                        wait = _SERVER_ERROR_BACKOFF_SECONDS * (attempt + 1)
                        print(f"[GEMINI] network error ({type(e).__name__}: {e}) - retry "
                              f"{attempt + 1}/{_SERVER_ERROR_RETRIES} in {wait}s...")
                        time.sleep(wait)
                        continue
                    raise RuntimeError(
                        f"Can't reach the Gemini API ({type(e).__name__}: {e}). "
                        f"Check internet / DNS / VPN / firewall, then use Retranslate."
                    ) from e

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

    def translate(self, texts_payload: list[dict], system_prompt: str | None = None) -> dict[str, str]:
        batch = self.request_json(system_prompt or self.default_system_prompt, texts_payload, TranslationBatch)
        return {item.id: item.translation for item in batch.translations}
