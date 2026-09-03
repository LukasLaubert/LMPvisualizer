"""Central logging setup for LMPvisualizer (stdlib only, no Qt).

Importing this module has no side effects. Call :func:`setup_logging`
once at application startup; afterwards use :func:`get_logger` to get
module-level loggers and :func:`log_exception` inside broad ``except``
paths (later Batch 4 migration target for ``print(...)`` diagnostics).
"""

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_DIR = Path.home() / ".LMPvisualizer"
LOG_FILE = LOG_DIR / "lmpvisualizer.log"

DEFAULT_LEVEL = logging.INFO
DEFAULT_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
DEFAULT_MAX_BYTES = 1_000_000
DEFAULT_BACKUP_COUNT = 3

_configured = False

__all__ = [
    "LOG_DIR",
    "LOG_FILE",
    "DEFAULT_LEVEL",
    "get_logger",
    "setup_logging",
    "log_exception",
]


def get_logger(name):
    """Return the stdlib logger for *name*.

    Does not configure any handler; call :func:`setup_logging` once at
    startup so records go to stdout and the rotating log file.
    """
    return logging.getLogger(name)


def setup_logging(level=DEFAULT_LEVEL, log_file=LOG_FILE,
                  max_bytes=DEFAULT_MAX_BYTES,
                  backup_count=DEFAULT_BACKUP_COUNT):
    """Configure root logging with stdout + rotating file handlers.

    Idempotent: repeated calls do not attach duplicate handlers (the
    level is still updated). Creates ``~/.LMPvisualizer`` on demand.
    Falls back to stdout-only if the file handler cannot be created.
    Returns the resolved log file path.
    """
    global _configured

    root = logging.getLogger()
    root.setLevel(level)

    formatter = logging.Formatter(DEFAULT_FORMAT)

    def _ensure_handler(handler):
        handler.setLevel(level)
        handler.setFormatter(formatter)
        root.addHandler(handler)

    if not _configured:
        _ensure_handler(logging.StreamHandler(sys.stdout))
        try:
            log_file = Path(log_file)
            log_file.parent.mkdir(parents=True, exist_ok=True)
            _ensure_handler(
                RotatingFileHandler(
                    str(log_file),
                    maxBytes=max_bytes,
                    backupCount=backup_count,
                    encoding="utf-8",
                )
            )
        except Exception as exc:
            root.warning("Could not create log file handler: %s", exc)
        _configured = True
    else:
        # Keep levels in sync without duplicating handlers.
        for handler in root.handlers:
            handler.setLevel(level)

    return Path(log_file)


def log_exception(logger, msg, *args):
    """Log *msg* with traceback for use in broad-except paths.

    Must be called from inside an ``except`` block; falls back to
    ``logger.error(..., exc_info=True)`` semantics via
    :meth:`logging.Logger.exception`.
    """
    if logger is None:
        logger = logging.getLogger()
    logger.exception(msg, *args)
