
# backend/services/providers/gemini_provider.py

"""
Gemini client: ONE request with ONE key (rotation, retry and cool-down live in pool.py).

The library's exceptions are mapped to the shared classes of base.py:
- ClientError 429 / RESOURCE_EXHAUSTED          -> RateLimited
- ClientError 400 API_KEY_INVALID / 401 / 403   -> KeyRejected
- ClientError 404 (unknown model)               -> KeyRejected
- httpx.TransportError / OSError (DNS, offline) -> Unreachable
- ServerError (5xx, overloaded)                 -> Transient
Any other client error propagates unchanged.

Uses response_schema (a Pydantic model) instead of a hand-described JSON shape in the prompt. Unlike Groq's
"json_object" mode - which only guarantees *some* valid JSON comes back - response_schema constrains generation to
that exact structure, so response.parsed can be trusted directly.

Thinking: Gemini 2.5 models "think" by default and those tokens count against max_output_tokens - a dense page
could end in a truncated, unparseable JSON. gemini_thinking_budget (settings) caps it; 0 = off on Flash.
"""

import httpx
from pydantic import BaseModel
from google import genai
from google.genai import types
from google.genai import errors as genai_errors

from core.config import get_setting
from .base import RateLimited, KeyRejected, Unreachable, Transient

HOST = "generativelanguage.googleapis.com"


def _thinking_config(model: str):
    """None = leave the model default. Only Gemini 2.5 takes a token budget (3.x uses thinking_level)."""
    budget = get_setting("gemini_thinking_budget", 0)
    if budget is None or budget < 0 or "2.5" not in model:
        return None
    if "pro" in model and budget < 128:
        budget = 128                     # 2.5 Pro cannot switch thinking off
    return types.ThinkingConfig(thinking_budget=int(budget))


def _classify(e: genai_errors.ClientError, model: str) -> Exception:
    code = getattr(e, "code", None)
    text = str(e)
    if code == 429:
        return RateLimited(f"Rate limit or quota reached (429 RESOURCE_EXHAUSTED): {text[:300]}")
    if code in (401, 403) or "API_KEY_INVALID" in text or "API key not valid" in text:
        return KeyRejected("Key rejected - wrong or revoked key. Copy it again from aistudio.google.com/apikey.")
    if code == 404:
        return KeyRejected(f"Model '{model}' not found (404) - pick one from Models.")
    return e


def call(entry: dict, system_prompt: str, user_message: str, schema: type[BaseModel], temperature: float,
         max_tokens: int) -> BaseModel:
    """One request with entry["api_key"] / entry["model"]. Raises base.KeyCallError subclasses (see module doc)."""
    model = entry["model"]
    client = genai.Client(api_key=entry["api_key"])
    config = dict(
        system_instruction=system_prompt,
        response_mime_type="application/json",
        response_schema=schema,
        temperature=temperature,
        max_output_tokens=max_tokens,
    )
    thinking = _thinking_config(model)
    if thinking is not None:
        config["thinking_config"] = thinking
    try:
        response = client.models.generate_content(
            model=model,
            contents=user_message,
            config=types.GenerateContentConfig(**config),
        )
    except genai_errors.ClientError as e:
        raise _classify(e, model) from e
    except genai_errors.ServerError as e:
        raise Transient(f"Gemini server error: {str(e)[:300]}") from e
    except (httpx.TransportError, OSError) as e:
        # DNS failure ("[Errno 11001] getaddrinfo failed"), no internet, VPN/firewall, timeout
        raise Unreachable(f"Can't reach {HOST} ({type(e).__name__}: {e}) - internet / DNS / VPN / firewall?") from e
    parsed = response.parsed
    if parsed is None:
        finish = ""
        try:
            finish = f" finish_reason={response.candidates[0].finish_reason}"
        except (AttributeError, IndexError, TypeError):
            pass
        raise RuntimeError(f"Gemini returned no parseable structured output{finish}. Raw: {response.text!r}")
    return parsed


def list_models(entry: dict) -> list[str]:
    """Model ids this key can use for generateContent."""
    client = genai.Client(api_key=entry["api_key"])
    try:
        found = list(client.models.list())
    except genai_errors.ClientError as e:
        raise _classify(e, "") from e
    except (httpx.TransportError, OSError) as e:
        raise Unreachable(f"Can't reach {HOST} ({type(e).__name__}).") from e
    ids = []
    for m in found:
        actions = getattr(m, "supported_actions", None) or []
        if actions and "generateContent" not in actions:
            continue
        name = (getattr(m, "name", "") or "").removeprefix("models/")
        if name and not any(s in name for s in ("embedding", "imagen", "veo", "tts", "aqa")):
            ids.append(name)
    return sorted(ids)
