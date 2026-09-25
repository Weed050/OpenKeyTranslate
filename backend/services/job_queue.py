
# backend/services/job_queue.py

"""
Minimal single-worker background job queue for OCR+translation prefetching.

This app is a synchronous, single-user local tool (see main.py's CORS
comment). A full task queue (Celery/RQ + broker) is overkill here - this
is a single ThreadPoolExecutor(max_workers=1) that guarantees only one
OCR/translation pipeline run happens at a time (the GPU-bound PaddleOCR
instance isn't safe to hit concurrently anyway), while letting it run
without blocking the request that triggered it.

Used for: prefetching the next page's OCR+translation while the user is
still reviewing/correcting the current page's bubbles in the editor - see
routers/pages.py's /process-async endpoint and editor.js's call to it
right after a page loads.
"""

from concurrent.futures import ThreadPoolExecutor
import threading

_executor = ThreadPoolExecutor(max_workers=1)
_queued_or_running: set[int] = set()
_lock = threading.Lock()


def enqueue_page_processing(page_id: int, run_fn) -> bool:
    """
    Schedule run_fn() (a zero-arg callable that fully processes one page)
    on the single background worker thread, unless page_id is already
    queued or currently running.

    :return: True if newly scheduled, False if already queued/running
        (caller should treat this as a harmless no-op, not an error).
    """
    with _lock:
        if page_id in _queued_or_running:
            return False
        _queued_or_running.add(page_id)

    def _wrapped():
        try:
            run_fn()
        finally:
            with _lock:
                _queued_or_running.discard(page_id)

    _executor.submit(_wrapped)
    return True


def is_queued_or_running(page_id: int) -> bool:
    with _lock:
        return page_id in _queued_or_running