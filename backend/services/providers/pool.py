
# backend/services/providers/pool.py

"""
The key pool: ONE ordered list of API keys that may mix providers, used as the translation backend.

Why: before 2026-11-04 only the keys of one "active provider" were used, so a free Groq key and a free Gemini key
could not back each other up, and every provider class carried its own copy of the rotation logic. Now the user
orders the keys in Settings and switches each one on or off; a request goes to the first usable key and moves on
along the list when a key is rate-limited, rejected or its server is unreachable:

- RateLimited  -> the key cools down 65 s (a 429 is usually per minute), next key.
- KeyRejected  -> the key is parked 10 min (wrong key / unknown model does not heal by retrying), next key.
- Transient    -> the same key is retried twice with a short backoff, then the next key.
- Unreachable  -> retried twice; then keys on the SAME host are skipped (no internet = every Groq key fails),
                  keys on other hosts (a local Ollama, another service) are still tried.
- anything else (bad request, unparseable reply) propagates at once - rotating would only hide the real cause.

Saving the Settings page rebuilds the pool (services/providers/__init__.get_provider compares a signature), which also
clears every cool-down - a key fixed by the user is used again at once.

pin = "provider/label": only that key is used (the A/B counterfactual of translation_service must go through the
SAME key and model as the shown translation). A pinned key that is cooling down is not even called - the experiment
call is skipped (logged as failed) instead of waiting a minute.
"""

import json
import time

from pydantic import BaseModel

from .base import TranslationProvider, RateLimited, KeyRejected, Unreachable, Transient

RETRIES = 2
BACKOFF_SECONDS = 2
RATE_LIMIT_COOLDOWN = 65.0
REJECTED_COOLDOWN = 600.0


def scrub(message: str, entry: dict) -> str:
    """Never let a key reach a message (some servers echo request details)."""
    key = (entry.get("api_key") or "").strip()
    return message.replace(key, key[:4] + "..." + key[-3:]) if len(key) >= 8 else message


class KeyPool(TranslationProvider):
    def __init__(self, entries: list[dict], clients: dict, host_of, temperature: float, max_tokens: int):
        """
        :param entries: usable keys in order of use - {"id", "provider", "label", "api_key", "model", "base_url"}.
        :param clients: provider name -> module with call(entry, system_prompt, user_message, schema, temp, max_tokens).
        :param host_of: entry -> host name (keys sharing a host share a network failure).
        """
        super().__init__()
        if not entries:
            raise ValueError("No translation key is switched on - add or switch one on in Settings > Translation & API keys.")
        self._entries = entries
        self._clients = clients
        self._host_of = host_of
        self._temperature = temperature
        self._max_tokens = max_tokens

    def request_json(self, system_prompt: str, payload, schema: type[BaseModel], pin: str | None = None) -> BaseModel:
        user_message = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)

        if pin is not None:
            candidates = [e for e in self._entries if e["id"] == pin]
            if not candidates:
                raise RuntimeError(f"Key '{pin}' is no longer in the pool.")
            if self._is_cooling(pin):
                raise RuntimeError(f"Key '{pin}' is cooling down after a rate limit - pinned call skipped.")
        else:
            candidates = [e for e in self._entries if not self._is_cooling(e["id"])]
            if not candidates:
                raise RuntimeError(f"No translation key available - every key is rate-limited or was rejected "
                                   f"(retry in ~{self._seconds_until_any_key()}s), or none is set.")

        errors: list[str] = []
        dead_hosts: set[str] = set()
        for entry in candidates:
            host = self._host_of(entry)
            if host and host in dead_hosts:
                continue
            client = self._clients[entry["provider"]]
            for attempt in range(RETRIES + 1):
                try:
                    result = client.call(entry, system_prompt, user_message, schema, self._temperature, self._max_tokens)
                except RateLimited as e:
                    print(f"[KEYS] '{entry['id']}' rate-limited - next key")
                    self._cool_down(entry["id"], RATE_LIMIT_COOLDOWN)
                    errors.append(f"{entry['label']}: {scrub(str(e), entry)}")
                    break
                except KeyRejected as e:
                    print(f"[KEYS] '{entry['id']}' rejected: {scrub(str(e), entry)} - next key")
                    self._cool_down(entry["id"], REJECTED_COOLDOWN)
                    errors.append(f"{entry['label']}: {scrub(str(e), entry)}")
                    break
                except (Unreachable, Transient) as e:
                    if attempt < RETRIES:
                        wait = BACKOFF_SECONDS * (attempt + 1)
                        print(f"[KEYS] '{entry['id']}' {type(e).__name__} - retry {attempt + 1}/{RETRIES} in {wait}s")
                        time.sleep(wait)
                        continue
                    if isinstance(e, Unreachable) and host:
                        dead_hosts.add(host)
                    errors.append(f"{entry['label']}: {scrub(str(e), entry)}")
                    break
                self.current_key_label = entry["label"]
                self.current_model = entry["model"]
                self.current_key_id = entry["id"]
                return result

        raise RuntimeError("No key could translate - " + " | ".join(errors) if errors else "No key could translate.")
