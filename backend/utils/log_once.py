
# backend/utils/log_once.py

"""
Print a diagnostic line once, not on every redraw.

Why: Lettering re-plans the whole page after every change (each drag, each font click) and the planner printed
"[BUBBLE GUARD] ..." and "[BUBBLE OVERFLOW] ..." for every bubble every time - a single overflowing bubble filled the
backend console with the same two lines (11.11.2026). The message is still printed the first time and again after
`every` seconds, so a problem that stays is not forgotten.
"""

import threading
import time

_SEEN: dict[str, float] = {}
_LOCK = threading.Lock()
_MAX = 2000                     # remembered messages; the oldest half is forgotten beyond this


def print_once(message: str, every: float = 600.0) -> bool:
    """Print `message` unless the same text was printed in the last `every` seconds. True = printed."""
    now = time.monotonic()
    with _LOCK:
        last = _SEEN.get(message)
        if last is not None and now - last < every:
            return False
        if len(_SEEN) >= _MAX:
            for key in sorted(_SEEN, key=_SEEN.get)[: _MAX // 2]:
                del _SEEN[key]
        _SEEN[message] = now
    print(message)
    return True
