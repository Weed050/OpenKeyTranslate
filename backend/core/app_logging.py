
# backend/core/app_logging.py

"""
Minimal application-level logging: mirrors everything the app prints to
console into rotating log files, split by day and by severity, and stamps
each run with the app version. Doesn't require touching every existing
print() call across the codebase - captures stdout/stderr at the stream level.

Two files are written per day:
  - app.log       everything (INFO and up) - full trace of a run
  - errors.log     ERROR and up only - fast triage without scrolling
    through OCR debug noise
Both rotate at midnight, one file per day, kept for BACKUP_DAYS days,
inside a per-month subfolder so <app_root_dir>/logs never accumulates an
unbounded flat pile of files.

Stepping stone, not the final form: real code should migrate print() ->
logging.getLogger(__name__) over time. This gets file-based logs +
rotation + version tracking working today without a large refactor.
"""

import sys
import logging
import logging.handlers
import os
from datetime import datetime

from core.config import APP_ROOT_DIR, APP_VERSION

BACKUP_DAYS = 14  # how many daily files to keep before TimedRotatingFileHandler deletes the oldest


class _StreamToLogger:
    """File-like shim: write() calls both the original stream and the logger."""
    def __init__(self, original_stream, logger, level):
        self._original_stream = original_stream
        self._logger = logger
        self._level = level

    def write(self, message):
        self._original_stream.write(message)
        message = message.strip()
        if message:
            self._logger.log(self._level, message)

    def flush(self):
        self._original_stream.flush()

    def isatty(self):
        return False


def _make_daily_handler(path: str, level: int) -> logging.handlers.TimedRotatingFileHandler:
    handler = logging.handlers.TimedRotatingFileHandler(
        path, when="midnight", backupCount=BACKUP_DAYS, encoding="utf-8"
    )
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    # dated backups as app-2026-09-26.log instead of the default app.log.2026-09-26
    handler.suffix = "%Y-%m-%d"
    return handler


def setup_logging():
    """
    Call once at app startup (see main.py), before anything else prints.
    Sets up two daily-rotating handlers under
    <app_root_dir>/logs/<YYYY-MM>/ (app.log = everything, errors.log =
    ERROR+ only), and mirrors stdout/stderr into both.
    """
    month_dir = os.path.join(APP_ROOT_DIR, "logs", datetime.now().strftime("%Y-%m"))
    os.makedirs(month_dir, exist_ok=True)

    app_log_path = os.path.join(month_dir, "app.log")
    error_log_path = os.path.join(month_dir, "errors.log")

    logger = logging.getLogger("openkey")
    logger.setLevel(logging.INFO)

    logger.addHandler(_make_daily_handler(app_log_path, logging.INFO))
    logger.addHandler(_make_daily_handler(error_log_path, logging.ERROR))
    logger.propagate = False

    sys.stdout = _StreamToLogger(sys.stdout, logger, logging.INFO)
    sys.stderr = _StreamToLogger(sys.stderr, logger, logging.ERROR)

    logger.info(f"===== OpenKeyTranslate v{APP_VERSION} starting =====")
    return app_log_path