
# backend/core/startup_checks.py

"""
Startup + on-demand configuration checks.

Runs once in main.py's lifespan (results are printed to the app log) and again on demand via
GET /system/health, which the frontend uses to show a banner. Checks never crash the app - the
user must still be able to open Settings / Projects to fix things; they only report.

level: "error" = translation will not work, "warn" = works but suspicious, "info" = FYI.
"""

import re
import socket

from core.config import (
    ACTIVE_PROVIDER, MEMORY_SIMILARITY_THRESHOLD, MEMORY_TOP_K, OCR_IGNORE_PATTERNS,
    PROVIDERS, SETTINGS_FILE, TRANSLATION_ON,
)

_PROVIDER_HOSTS = {
    "gemini": "generativelanguage.googleapis.com",
    "groq": "api.groq.com",
}


def _dns_ok(host: str, timeout: float = 3.0) -> tuple[bool, str]:
    old = socket.getdefaulttimeout()
    socket.setdefaulttimeout(timeout)
    try:
        socket.getaddrinfo(host, 443)
        return True, ""
    except OSError as e:
        return False, str(e)
    finally:
        socket.setdefaulttimeout(old)


def run_checks(check_network: bool = True) -> list[dict]:
    issues: list[dict] = []

    def add(level: str, code: str, message: str):
        issues.append({"level": level, "code": code, "message": message})

    if not TRANSLATION_ON:
        add("info", "translation_off", "translation_on=false: pages are OCR'd but not translated.")
    else:
        cfg = PROVIDERS.get(ACTIVE_PROVIDER)
        if cfg is None:
            add("error", "unknown_provider",
                f"active_provider '{ACTIVE_PROVIDER}' has no entry under 'providers' in {SETTINGS_FILE}.")
        else:
            keys = cfg.get("keys", [])
            filled = [k for k in keys if (k.get("api_key") or "").strip()]
            if not cfg.get("model"):
                add("error", "no_model", f"providers.{ACTIVE_PROVIDER}.model is empty.")
            if not filled:
                add("error", "no_api_key",
                    f"No api_key for '{ACTIVE_PROVIDER}' - translation will fail. Fill providers.{ACTIVE_PROVIDER}.keys in {SETTINGS_FILE} and restart.")
            else:
                labels = [k.get("label", "default") for k in filled]
                if len(set(labels)) != len(labels):
                    add("warn", "duplicate_key_labels",
                        "Two keys share the same label - rotation marks BOTH exhausted when one hits a rate limit.")
                add("info", "keys_ok", f"{len(filled)} {ACTIVE_PROVIDER} key(s) configured.")
            if len(filled) < len(keys):
                add("warn", "empty_key_slot", f"{len(keys) - len(filled)} key slot(s) in '{ACTIVE_PROVIDER}' have an empty api_key.")

            host = _PROVIDER_HOSTS.get(ACTIVE_PROVIDER)
            if check_network and host:
                ok, err = _dns_ok(host)
                if not ok:
                    add("error", "api_unreachable",
                        f"Can't resolve {host} ({err}). No internet / DNS / VPN? OCR still works; translation will fail until this is fixed.")

    if not (0.0 <= MEMORY_SIMILARITY_THRESHOLD <= 1.0):
        add("warn", "bad_threshold", f"memory_similarity_threshold={MEMORY_SIMILARITY_THRESHOLD} is outside 0..1.")
    if MEMORY_TOP_K < 1:
        add("warn", "bad_top_k", "memory_top_k must be >= 1.")

    for pattern in OCR_IGNORE_PATTERNS:
        try:
            re.compile(pattern)
        except re.error as e:
            add("warn", "bad_ignore_pattern", f"ocr_ignore_patterns: invalid regex {pattern!r}: {e}")

    return issues


def log_startup_report() -> list[dict]:
    issues = run_checks()
    for i in issues:
        print(f"[STARTUP CHECK] {i['level'].upper():<5} {i['code']}: {i['message']}")
    if not any(i["level"] in ("error", "warn") for i in issues):
        print("[STARTUP CHECK] all good.")
    return issues