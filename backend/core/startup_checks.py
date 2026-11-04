
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

from core.config import get_setting

_LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1", "0.0.0.0")


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
    # LIVE values: /system/health is polled by the UI, so it must reflect the Settings page immediately.
    TRANSLATION_ON = get_setting("translation_on", True)
    MEMORY_SIMILARITY_THRESHOLD = get_setting("memory_similarity_threshold", 0.67)
    MEMORY_TOP_K = get_setting("memory_top_k", 3)
    OCR_IGNORE_PATTERNS = get_setting("ocr_ignore_patterns", []) or []

    def add(level: str, code: str, message: str):
        issues.append({"level": level, "code": code, "message": message})

    if not TRANSLATION_ON:
        add("info", "translation_off", "translation_on=false: pages are OCR'd but not translated.")
    else:
        # the key pool (services/providers): keys switched on, in order of use, possibly of several providers
        from services.providers import pool_entries, unusable_reason, host_of
        switched_on = pool_entries()
        usable = [e for e in switched_on if not unusable_reason(e)]
        if not usable:
            add("error", "no_api_key",
                "No usable translation key - translation will fail. Paste a key (or switch one on) in Settings > "
                "Translation & API keys (saved at once, no restart).")
        else:
            kinds = {}
            for e in usable:
                kinds[e["provider"]] = kinds.get(e["provider"], 0) + 1
            add("info", "keys_ok", f"{len(usable)} key(s) in the translation pool ("
                + ", ".join(f"{n} {p}" for p, n in kinds.items()) + ").")
        broken = [e for e in switched_on if unusable_reason(e)]
        if broken:
            add("warn", "empty_key_slot", "Switched on but not usable: "
                + ", ".join(f"{e['label']} ({e['provider']}: {unusable_reason(e)})" for e in broken)
                + " - fix, switch off or remove them in Settings > Translation & API keys.")

        hosts = sorted({host_of(e) for e in usable} - {""} - set(_LOCAL_HOSTS))
        if check_network and hosts:
            failed = [(h, err) for h in hosts for ok, err in [_dns_ok(h)] if not ok]
            if failed and len(failed) == len(hosts):
                add("error", "api_unreachable",
                    f"Can't resolve {', '.join(h for h, _ in failed)} ({failed[0][1]}). No internet / DNS / VPN? "
                    f"OCR still works; translation will fail until this is fixed.")
            elif failed:
                add("warn", "api_unreachable",
                    f"Can't resolve {', '.join(h for h, _ in failed)} - keys on that server are skipped, the others still work.")

    from core.app_logging import LAST_CRASH
    if LAST_CRASH:
        add("warn", "native_crash",
            "The last run of the backend crashed inside a library (" + LAST_CRASH.splitlines()[0] + "). "
            "If it was the OCR: Settings > OCR engine (detection model, oneDNN) and backend/scripts/ocr_selftest.py. "
            "Details: logs/crash.log.")

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