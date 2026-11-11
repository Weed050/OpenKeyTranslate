
# backend/services/providers/key_usage.py

"""
How much of each key's quota is used - an estimate shown under the key in Settings.

Why: free keys run out (Gemini: requests per DAY per model, reset at midnight Pacific time; Groq: requests per day
and tokens per minute) and the app only found out at the next 429. Nobody tells the app the remaining quota up
front except Groq, so this keeps:
  * the requests THIS app made with each key today (the pool counts every successful call - page translation, A/B
    counterfactual, retranslate, Test, the research replay script), per key AND model - Gemini limits are per model;
  * the limit when a server names it: Groq sends x-ratelimit-limit-requests / -remaining-requests with every answer
    (exact), Gemini names the limit in its 429 ("...PerDay... limit: 250"); without a number, the count reached when the
    day's quota ran out is kept as an estimate;
  * the last 429 (per minute / per day) and when.
Requests made with the same key elsewhere (another app, AI Studio) are not seen - "left" is an estimate, the
server's word (Groq) wins. Display data only: nothing here decides which key is used (that is pool.py's cool-down).
Stored in <app root>/key_usage.json - key ids ("provider/label") and numbers, never the key itself.
"""

import json
import os
import threading
import time
from datetime import datetime, timedelta, timezone

from core.config import APP_ROOT_DIR

USAGE_FILE = os.path.join(APP_ROOT_DIR, "key_usage.json")
"""@private path in the user's data folder"""
KEEP_DAYS = 8                   # daily counts kept per key (a week back for the tooltip)
_LOCK = threading.Lock()
_minute: dict[str, list[float]] = {}        # request times of the last minute, per key - memory only
_data: dict | None = None


def _pacific_today() -> str:
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/Los_Angeles")).date().isoformat()
    except Exception:                                    # noqa: BLE001 - no tz database: standard time is close enough
        return (datetime.now(timezone.utc) - timedelta(hours=8)).date().isoformat()


def day_of(provider: str) -> str:
    """The quota day of a provider: Gemini counts in Pacific time, the others in UTC."""
    return _pacific_today() if provider == "gemini" else datetime.now(timezone.utc).date().isoformat()


def _slot_id(entry: dict) -> str:
    return f"{entry['id']}|{entry.get('model') or ''}"


def _load() -> dict:
    global _data
    if _data is None:
        try:
            with open(USAGE_FILE, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            _data = loaded if isinstance(loaded, dict) and isinstance(loaded.get("slots"), dict) else {"slots": {}}
        except (OSError, ValueError):
            _data = {"slots": {}}
    return _data


def _save() -> None:
    try:
        os.makedirs(os.path.dirname(USAGE_FILE), exist_ok=True)
        tmp = USAGE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, USAGE_FILE)
    except OSError as e:
        print(f"[KEYS] couldn't save {os.path.basename(USAGE_FILE)}: {e}")


def _slot(entry: dict) -> dict:
    data = _load()
    slot = data["slots"].setdefault(_slot_id(entry), {})
    slot["key"], slot["provider"], slot["model"] = entry["id"], entry["provider"], entry.get("model") or ""
    days = slot.setdefault("days", {})
    for old in sorted(days)[:-KEEP_DAYS]:
        del days[old]
    return slot


def record_success(entry: dict) -> None:
    """One answered request through this key (pool.py). Never raises."""
    try:
        with _LOCK:
            slot = _slot(entry)
            day = day_of(entry["provider"])
            slot["days"][day] = slot["days"].get(day, 0) + 1
            slot["last_used"] = time.time()
            now = time.time()
            recent = [t for t in _minute.get(_slot_id(entry), []) if now - t < 60] + [now]
            _minute[_slot_id(entry)] = recent
            _save()
    except Exception as e:                                       # noqa: BLE001 - display data only
        print(f"[KEYS] usage not counted: {e}")


def record_server_counts(entry: dict, limit_day: int | None, left_day: int | None) -> None:
    """What the server says with an answer (Groq x-ratelimit-*-requests): the exact day limit and what is left."""
    if limit_day is None and left_day is None:
        return
    try:
        with _LOCK:
            slot = _slot(entry)
            if limit_day is not None:
                slot["limit_day"], slot["limit_source"] = int(limit_day), "server"
            if left_day is not None:
                slot["server_left"] = {"day": day_of(entry["provider"]), "left": int(left_day), "at": time.time()}
            _save()
    except Exception as e:                                       # noqa: BLE001
        print(f"[KEYS] usage not noted: {e}")


def record_limit(entry: dict, daily: bool, limit: int | None, metric: str | None) -> None:
    """A 429 (pool.py): which limit, and its number when the server named one. Never raises."""
    try:
        with _LOCK:
            slot = _slot(entry)
            day = day_of(entry["provider"])
            slot["last_limit"] = {"at": time.time(), "daily": bool(daily), "metric": metric or "", "limit": limit}
            if daily:
                slot["exhausted"] = day
                if limit:
                    slot["limit_day"], slot["limit_source"] = int(limit), "server"
                elif slot.get("limit_source") != "server":
                    # no number from the server: the count reached when it ran out is the best estimate
                    slot["limit_day"], slot["limit_source"] = max(1, slot["days"].get(day, 0)), "estimate"
            elif limit:
                slot["limit_minute"] = int(limit)
            _save()
    except Exception as e:                                       # noqa: BLE001
        print(f"[KEYS] limit not noted: {e}")


def summary() -> dict:
    """
    {key id: {"model", "today", "day", "limit_day", "limit_source", "left", "exhausted_today", "last_minute",
    "limit_minute", "last_limit", "week"}} for the keys' CURRENT models - GET /settings/key-usage.
    "left" = the server's own number when it sent one today (Groq), else limit - today; None without a limit.
    """
    from . import pool_entries
    out = {}
    with _LOCK:
        data = _load()
        now = time.time()
        for entry in pool_entries(include_disabled=True):
            slot = data["slots"].get(_slot_id(entry))
            if not slot:
                continue
            day = day_of(entry["provider"])
            today = slot.get("days", {}).get(day, 0)
            limit = slot.get("limit_day")
            server = slot.get("server_left") or {}
            exhausted = slot.get("exhausted") == day
            if server.get("day") == day:
                left = int(server["left"])
            elif limit:
                left = max(0, int(limit) - today)
            else:
                left = None
            if exhausted:
                left = 0
            out[entry["id"]] = {
                "model": slot.get("model", ""), "today": today, "day": day, "limit_day": limit,
                "limit_source": slot.get("limit_source"), "left": left, "exhausted_today": exhausted,
                "last_minute": len([t for t in _minute.get(_slot_id(entry), []) if now - t < 60]),
                "limit_minute": slot.get("limit_minute"), "last_limit": slot.get("last_limit"),
                "last_used": slot.get("last_used"), "week": dict(sorted(slot.get("days", {}).items())),
            }
    return out
