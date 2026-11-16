
# backend/services/run_progress.py

"""
What a page run (OCR -> translation -> cleaning) is doing right now - the editor's progress panel instead of a static
"Processing, this can take a while..." for 10-60 s (the user asked what is happening on the backend, 13.11.2026).

Why a context variable and not a page_id passed down: the steps that know something worth showing sit deep inside
other modules (the slice loop of the OCR, the key pool waiting after a rate limit), and threading a page id through
all of them would change a dozen signatures for a display. `track(page_id)` marks the thread that runs the page
(the request's worker thread or the background job's thread - every call below happens inside it); `step` / `advance`
/ `note` called anywhere in that thread update that page's entry, and do nothing outside a tracked run (bubble edits,
the replay script, tests).

Only in memory: a run that ends (or the backend restarting) removes its entry; GET /pages/{id}/status returns it as
`progress` while it runs. Nothing here is logged or stored - it is a display, not research data.
"""

import contextvars
import threading
import time
from contextlib import contextmanager

STEPS = ("load", "ocr", "translate", "clean", "save")
"""Order of the steps of a page run; the editor names them (js/editor-progress.js)."""

_CURRENT: contextvars.ContextVar[int | None] = contextvars.ContextVar("okt_run_page", default=None)
_RUNS: dict[int, dict] = {}
_LOCK = threading.Lock()


@contextmanager
def track(page_id: int, kind: str = "process", translate: bool = True):
    """
    Marks the current thread as running page `page_id` until the block ends. kind: "process" | "reocr".
    translate=False (prefetch: OCR + cleaning only) leaves the translation step out of the list.
    """
    now = time.time()
    steps = [s for s in STEPS if translate or s != "translate"]
    with _LOCK:
        _RUNS[page_id] = {"kind": kind, "steps": steps, "step": steps[0], "started": now, "step_started": now,
                          "done": None, "total": None, "detail": ""}
    token = _CURRENT.set(page_id)
    try:
        yield
    finally:
        _CURRENT.reset(token)
        with _LOCK:
            _RUNS.pop(page_id, None)


def _update(**fields) -> None:
    page_id = _CURRENT.get()
    if page_id is None:
        return
    with _LOCK:
        run = _RUNS.get(page_id)
        if run is not None:
            run.update(fields)


def step(name: str, detail: str = "") -> None:
    """The run moved on to step `name` (one of STEPS); clears the counter of the step before."""
    _update(step=name, step_started=time.time(), done=None, total=None, detail=detail)


def advance(done: float, total: float, detail: str | None = None) -> None:
    """Progress inside the current step (e.g. rows of a webtoon strip read so far / its height)."""
    fields = {"done": float(done), "total": float(total)}
    if detail is not None:
        fields["detail"] = detail
    _update(**fields)


def note(detail: str) -> None:
    """A line about what the step waits for right now (a key resting after its limit, a retry) - shown under it."""
    _update(detail=detail)


def snapshot(page_id: int) -> dict | None:
    """The running page's progress for GET /pages/{id}/status, or None when no run of this page is going on."""
    with _LOCK:
        run = _RUNS.get(page_id)
        if run is None:
            return None
        run = dict(run)
    now = time.time()
    run["elapsed"] = round(now - run.pop("started"), 1)
    run["step_elapsed"] = round(now - run.pop("step_started"), 1)
    return run
