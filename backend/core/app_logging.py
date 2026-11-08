
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

A third file, logs/crash.log, catches NATIVE crashes (faulthandler): an access violation inside Paddle / OpenCV kills
the process without a Python exception, so app.log only shows the last line before it. faulthandler writes the Python
stack of every thread at the moment of the crash; the next start reports it (LAST_CRASH -> health banner).

Stepping stone, not the final form: real code should migrate print() ->
logging.getLogger(__name__) over time. This gets file-based logs +
rotation + version tracking working today without a large refactor.
"""

import faulthandler
import re
import sys
import logging
import logging.handlers
import os
from datetime import datetime

from core.config import APP_ROOT_DIR, APP_VERSION

BACKUP_DAYS = 14  # how many daily files to keep before TimedRotatingFileHandler deletes the oldest
CRASH_LOG_MAX = 200_000   # bytes; older crash traces are cut off at startup
LAST_CRASH: str | None = None   # the native crash of the previous run (its fault line + first stack lines), or None
_crash_file = None              # kept open for the whole run: faulthandler writes into it at the moment of a crash

# Windows reports C++ exceptions that Paddle throws AND catches itself (code 0xe06d7363) through the same handler;
# only these mean the process really died
_FATAL_MARKERS = ("access violation", "stack overflow", "illegal instruction", "Fatal Python error", "in page error")


_ANSI = re.compile(r"\x1b\[([0-9;]*)m")
# uvicorn writes its own level in front ("INFO:     127.0.0.1 ...") - to stderr, like every library below
_PREFIX_LEVEL = re.compile(r"^(DEBUG|INFO|WARNING|ERROR|CRITICAL):\s+")
# Python warnings ("...py:113: RequestsDependencyWarning: ...") and progress bars ("Loading weights: 100%|###| 103/103")
_WARNING = re.compile(r"\b\w*Warning: ")
_PROGRESS = re.compile(r"\d+%\|.*\|")


def _classify(message: str, default: int) -> tuple[int, str]:
    """
    The level a line written to a stream really has, and the line without colour codes.

    Everything on stderr used to be logged as ERROR: the log viewer and errors.log were full of "ERROR" lines that were
    uvicorn's request lines, Paddle's green "Creating model" notes, library warnings and progress bars - a real error
    was hard to find. Known kinds get their own level; anything else on stderr stays ERROR (a traceback is unknown text).
    """
    colours = _ANSI.findall(message)
    text = _ANSI.sub("", message)
    prefix = _PREFIX_LEVEL.match(text)
    if prefix:
        return logging.getLevelName(prefix.group(1)), text[prefix.end():]
    if default <= logging.INFO:
        return default, text
    if _WARNING.search(text):
        return logging.WARNING, text
    if _PROGRESS.search(text):
        return logging.INFO, text
    codes = {c for group in colours for c in group.split(";") if c}
    if "31" in codes or "91" in codes:      # red = the library's own error
        return logging.ERROR, text
    if "33" in codes or "93" in codes:      # yellow = warning
        return logging.WARNING, text
    if codes:                               # green / other colours = notes (paddlex "Creating model: ...")
        return logging.INFO, text
    return default, text


class _StreamToLogger:
    """File-like shim: write() calls both the original stream and the logger (level per line, see _classify)."""
    def __init__(self, original_stream, logger, level):
        self._original_stream = original_stream
        self._logger = logger
        self._level = level

    def write(self, message):
        self._original_stream.write(message)
        message = message.strip()
        if message:
            level, text = _classify(message, self._level)
            self._logger.log(level, text)

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


def _enable_crash_traces(logs_dir: str) -> None:
    """faulthandler -> logs/crash.log; remember whether the previous run ended in a native crash."""
    global LAST_CRASH, _crash_file
    path = os.path.join(logs_dir, "crash.log")
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            old = f.read()
    except FileNotFoundError:
        old = ""
    last_run = old.rsplit("===== run ", 1)[-1] if "===== run " in old else ""
    fatal = [l for l in last_run.splitlines() if any(m in l for m in _FATAL_MARKERS)]
    if fatal:
        stack = [l.strip() for l in last_run[last_run.find(fatal[-1]):].splitlines() if l.strip().startswith("File ")][:4]
        LAST_CRASH = "\n".join([fatal[-1].strip(), *stack])
    try:
        if len(old) > CRASH_LOG_MAX:
            with open(path, "w", encoding="utf-8") as f:
                f.write(old[-CRASH_LOG_MAX // 2:])
        _crash_file = open(path, "a", encoding="utf-8")
        _crash_file.write(f"\n===== run {datetime.now():%Y-%m-%d %H:%M:%S} pid {os.getpid()} v{APP_VERSION} =====\n")
        _crash_file.flush()
        faulthandler.enable(file=_crash_file, all_threads=True)
    except OSError as e:
        print(f"[LOG] crash traces off: {e}")


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

    _enable_crash_traces(os.path.join(APP_ROOT_DIR, "logs"))

    sys.stdout = _StreamToLogger(sys.stdout, logger, logging.INFO)
    sys.stderr = _StreamToLogger(sys.stderr, logger, logging.ERROR)

    logger.info(f"===== OpenKeyTranslate v{APP_VERSION} starting =====")
    if LAST_CRASH:
        logger.error(f"[CRASH] the previous run ended in a native crash (logs/crash.log): {LAST_CRASH}")
    return app_log_path