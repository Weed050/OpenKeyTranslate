
# backend/services/providers/groq_provider.py

"""
Groq client: ONE request with ONE key (rotation, retry and cool-down live in pool.py).

The library's exceptions are mapped to the shared classes of base.py, so the pool can tell them apart:
- RateLimitError (429)                         -> RateLimited  (this key's quota is spent for now)
- AuthenticationError / PermissionDenied / 404 -> KeyRejected  (wrong or revoked key, unknown model)
- APIConnectionError (DNS, offline, timeout)   -> Unreachable
- InternalServerError (5xx, overloaded)        -> Transient    (usually gone within seconds)
Anything else (bad request, malformed JSON reply) propagates unchanged - neither retrying nor rotating fixes it.

Uses "json_object" response mode - this guarantees syntactically valid JSON, but not the exact key names/shape,
hence the defensive normalisation of a translation batch below (contrast with gemini_provider.py, which enforces
a real schema).
"""

import json
import re

from groq import (Groq, RateLimitError, InternalServerError, APIConnectionError, AuthenticationError,
                  PermissionDeniedError, NotFoundError)
from pydantic import BaseModel

from .base import TranslationBatch, RateLimited, KeyRejected, Unreachable, Transient

HOST = "api.groq.com"


def normalise_batch(data: dict, schema: type[BaseModel]) -> dict:
    """json_object mode does not enforce the shape: keep only well-formed items instead of failing the page."""
    if schema is TranslationBatch:
        items = [t for t in data.get("translations", []) if isinstance(t, dict) and "id" in t]
        data = {"translations": [{"id": str(t["id"]), "translation": str(t.get("translation", ""))} for t in items]}
    return data


def _short(e: Exception) -> str:
    text = str(getattr(e, "message", "") or e)
    return text if len(text) < 300 else text[:300] + "..."


_GROQ_LIMIT = re.compile(r"on (requests|tokens) per (day|minute)[^:]*:\s*Limit (\d+)", re.I)


def rate_limit_from(text: str) -> RateLimited:
    """A Groq 429 ("... on requests per day (RPD): Limit 1000, Used 1000 ...") -> RateLimited with the limit named.
    The organization id in the text is not kept in the message."""
    m = _GROQ_LIMIT.search(text)
    daily = bool(m and m.group(2).lower() == "day") or "per day" in text.lower()
    metric = f"{m.group(1).lower()} per {m.group(2).lower()}" if m else None
    limit = int(m.group(3)) if m and m.group(1).lower() == "requests" else None
    kind = (f"{metric}, limit {m.group(3)}" if m else "limit reached")
    return RateLimited(f"Rate limit or quota reached (429: {kind})", daily=daily, limit=limit, metric=metric)


def _header_int(headers, name: str) -> int | None:
    try:
        return int(float(headers.get(name)))
    except (TypeError, ValueError):
        return None


def call(entry: dict, system_prompt: str, user_message: str, schema: type[BaseModel], temperature: float,
         max_tokens: int) -> BaseModel:
    """One request with entry["api_key"] / entry["model"]. Raises base.KeyCallError subclasses (see module doc)."""
    # the SDK's own short retries (Retry-After of a 429) stay on, as before the pool: a single-key user would
    # otherwise wait out a full cool-down for a burst the SDK rides out in seconds
    client = Groq(api_key=entry["api_key"])
    try:
        # raw response: its x-ratelimit-* headers say the day's request limit and what is left (Settings, key_usage)
        raw_response = client.chat.completions.with_raw_response.create(
            model=entry["model"],
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            response_format={"type": "json_object"},
            temperature=temperature,
            max_tokens=max_tokens,
        )
        response = raw_response.parse()
    except RateLimitError as e:
        raise rate_limit_from(_short(e)) from e
    except AuthenticationError as e:
        raise KeyRejected("Key rejected (401) - wrong or revoked key. Copy it again from console.groq.com/keys.") from e
    except PermissionDeniedError as e:
        raise KeyRejected(f"No permission (403): {_short(e)}") from e
    except NotFoundError as e:
        raise KeyRejected(f"Model '{entry['model']}' not found (404) - pick one from Models.") from e
    except APIConnectionError as e:
        raise Unreachable(f"Can't reach {HOST} ({type(e).__name__}) - internet / DNS / VPN / firewall?") from e
    except InternalServerError as e:
        raise Transient(f"Groq server error: {_short(e)}") from e
    try:
        from .key_usage import record_server_counts
        record_server_counts(entry, _header_int(raw_response.headers, "x-ratelimit-limit-requests"),
                             _header_int(raw_response.headers, "x-ratelimit-remaining-requests"))
    except Exception:                                           # noqa: BLE001 - display data only
        pass
    raw = response.choices[0].message.content.strip()
    return schema.model_validate(normalise_batch(json.loads(raw), schema))


def list_models(entry: dict) -> list[str]:
    """Model ids this key can use (Groq's /models)."""
    client = Groq(api_key=entry["api_key"], max_retries=0)
    try:
        models = client.models.list()
    except AuthenticationError as e:
        raise KeyRejected("Key rejected (401) - wrong or revoked key.") from e
    except APIConnectionError as e:
        raise Unreachable(f"Can't reach {HOST} ({type(e).__name__}).") from e
    ids = [m.id for m in getattr(models, "data", []) if getattr(m, "active", True) is not False]
    # speech / guard / embedding models cannot translate a page
    skip = ("whisper", "guard", "tts", "embed", "distil")
    return sorted(i for i in ids if not any(s in i.lower() for s in skip))
