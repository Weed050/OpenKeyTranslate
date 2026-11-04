
# backend/services/providers/openai_compat_provider.py

"""
Generic "OpenAI-compatible" client: ONE request with ONE key to any server that speaks the OpenAI chat-completions
API - OpenRouter, Mistral, DeepSeek, OpenAI itself, or a local Ollama / LM Studio (no key needed there).

Why a generic client instead of one class per service: these services differ only in the base URL, the key and the
model names, and a BYOK thesis tool cannot follow every vendor SDK. Plain httpx (already a dependency) keeps it
library-free. NOT tested against every server by the author - the Settings page says so and asks for a GitHub report.

Requests: POST {base_url}/chat/completions with response_format json_object. Servers that refuse that field (400
mentioning response_format) are asked again without it - the prompts already demand "ONLY a JSON object" - and the
base URL is remembered for the rest of the run. The reply is parsed tolerantly (code fences, text around the JSON),
because a local model does not always obey.

Errors are mapped to the shared classes of base.py (429 -> RateLimited, 401/403/404 -> KeyRejected, no connection ->
Unreachable, 5xx -> Transient), so the pool rotates the same way as for Groq / Gemini.
"""

import json
import re
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel

from .base import RateLimited, KeyRejected, Unreachable, Transient
from .groq_provider import normalise_batch

# local models can need minutes for a dense page; connecting must still fail fast when nothing listens
_TIMEOUT = httpx.Timeout(connect=10.0, read=600.0, write=30.0, pool=10.0)
_NO_JSON_MODE: set[str] = set()      # base URLs that rejected response_format (asked without it from then on)

PRESETS = [
    {"name": "OpenRouter", "base_url": "https://openrouter.ai/api/v1"},
    {"name": "Mistral", "base_url": "https://api.mistral.ai/v1"},
    {"name": "DeepSeek", "base_url": "https://api.deepseek.com/v1"},
    {"name": "OpenAI", "base_url": "https://api.openai.com/v1"},
    {"name": "Ollama (local)", "base_url": "http://localhost:11434/v1"},
    {"name": "LM Studio (local)", "base_url": "http://localhost:1234/v1"},
]


def base_url_of(entry: dict) -> str:
    base = (entry.get("base_url") or "").strip().rstrip("/")
    return base.removesuffix("/chat/completions")


def host_of(entry: dict) -> str:
    return urlparse(base_url_of(entry)).hostname or ""


def is_local(entry: dict) -> bool:
    return host_of(entry) in ("localhost", "127.0.0.1", "::1", "0.0.0.0")


def _headers(entry: dict) -> dict:
    key = (entry.get("api_key") or "").strip()
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    return headers


def _unreachable(entry: dict, e: Exception) -> Unreachable:
    hint = (" - is Ollama / LM Studio running and its server started?" if is_local(entry)
            else " - internet / DNS / VPN / firewall, or a typo in the base URL?")
    return Unreachable(f"Nothing answers at {base_url_of(entry)} ({type(e).__name__}){hint}")


def _raise_for_status(entry: dict, response: httpx.Response) -> None:
    code = response.status_code
    if code < 400:
        return
    detail = response.text[:300]
    if code == 429:
        raise RateLimited(f"Rate limit or quota reached (429): {detail}")
    if code in (401, 403):
        raise KeyRejected(f"Key rejected ({code}) - wrong key, or this service needs one: {detail}")
    if code == 404:
        raise KeyRejected(f"Not found (404) - check the model name '{entry.get('model')}' and that the base URL "
                          f"ends with /v1: {detail}")
    if code >= 500:
        raise Transient(f"Server error {code}: {detail}")
    raise RuntimeError(f"HTTP {code} from {base_url_of(entry)}: {detail}")


def parse_json_reply(text: str) -> dict:
    """The JSON object of a reply that may come in ```json fences or with words around it."""
    text = (text or "").strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fenced:
        text = fenced.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start:end + 1])
        raise


def call(entry: dict, system_prompt: str, user_message: str, schema: type[BaseModel], temperature: float,
         max_tokens: int) -> BaseModel:
    """One request with entry["base_url"] / entry["api_key"] (may be empty) / entry["model"]."""
    base = base_url_of(entry)
    if not base:
        raise KeyRejected("No base URL - enter the server address (e.g. https://openrouter.ai/api/v1).")
    body = {
        "model": entry["model"],
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    json_mode = base not in _NO_JSON_MODE
    for _ in range(2):
        payload = dict(body, response_format={"type": "json_object"}) if json_mode else body
        try:
            response = httpx.post(f"{base}/chat/completions", headers=_headers(entry), json=payload, timeout=_TIMEOUT)
        except httpx.TransportError as e:
            raise _unreachable(entry, e) from e
        if json_mode and response.status_code in (400, 422) and "response_format" in response.text:
            _NO_JSON_MODE.add(base)
            json_mode = False
            continue
        _raise_for_status(entry, response)
        break
    try:
        content = response.json()["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError) as e:
        raise RuntimeError(f"Unexpected reply from {base}: {response.text[:300]!r}") from e
    return schema.model_validate(normalise_batch(parse_json_reply(content), schema))


def list_models(entry: dict) -> list[str]:
    """Model ids the server offers (GET {base_url}/models - OpenAI format, also Ollama / LM Studio)."""
    base = base_url_of(entry)
    if not base:
        raise KeyRejected("No base URL.")
    try:
        response = httpx.get(f"{base}/models", headers=_headers(entry), timeout=httpx.Timeout(15.0))
    except httpx.TransportError as e:
        raise _unreachable(entry, e) from e
    _raise_for_status(entry, response)
    try:
        return sorted(str(m["id"]) for m in response.json().get("data", []) if m.get("id"))
    except (ValueError, AttributeError, TypeError) as e:
        raise RuntimeError(f"Unexpected /models reply from {base}: {response.text[:200]!r}") from e
