
# backend/core/app_logging.py
"""
Minimal application-level logging: mirrors everything the app prints to
console into a rotating log file too, and stamps each run with the app
version. Doesn't require touching every existing print() call across the
codebase - captures stdout/stderr at the stream level.

Stepping stone, not the final form: real code should migrate print() ->
logging.getLogger(__name__) over time. This gets file-based logs +
rotation + version tracking working today without a large refactor.
"""

import sys
import logging
import logging.handlers
import os

from core.config import APP_ROOT_DIR, APP_VERSION


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


def setup_logging():
    """
    Call once at app startup (see main.py), before anything else prints.
    Sets up a rotating file handler (5 MB per file, 5 backups) under
    <app_root_dir>/logs/app.log, and mirrors stdout/stderr into it.
    """
    log_dir = os.path.join(APP_ROOT_DIR, "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, "app.log")

    logger = logging.getLogger("openkey")
    logger.setLevel(logging.INFO)

    handler = logging.handlers.RotatingFileHandler(
        log_path, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    logger.propagate = False

    sys.stdout = _StreamToLogger(sys.stdout, logger, logging.INFO)
    sys.stderr = _StreamToLogger(sys.stderr, logger, logging.ERROR)

    logger.info(f"===== OpenKeyTranslate v{APP_VERSION} starting =====")
    return log_path