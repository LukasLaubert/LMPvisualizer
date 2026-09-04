"""Central logging setup for LMPvisualizer (stdlib only, no Qt).

Importing this module has no side effects. Call :func:`setup_logging`
once at application startup; afterwards use :func:`get_logger` to get
module-level loggers and :func:`log_exception` inside broad ``except``
paths. Console output stays plain ("[System] ..."). File logging is
available but disabled (same behavior as before v0.7.0); flip
:const:`FILE_LOGGING_ENABLED` to turn it back on.
"""

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_DIR = Path.home() / ".LMPvisualizer"
LOG_FILE = LOG_DIR / "lmpvisualizer.log"

# File logging available, but off unless explicitly enabled.
FILE_LOGGING_ENABLED = False

DEFAULT_LEVEL = logging.INFO
CONSOLE_FORMAT = "%(message)s"
FILE_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
DEFAULT_MAX_BYTES = 1_000_000
DEFAULT_BACKUP_COUNT = 3

_configured = False

__all__ = [
    "LOG_DIR",
    "LOG_FILE",
    "FILE_LOGGING_ENABLED",
    "DEFAULT_LEVEL",
    "get_logger",
    "setup_logging",
    "log_exception",
]


def get_logger(name):
    """Return the stdlib logger for *name*.

    Does not configure any handler; call :func:`setup_logging` once at
    startup so records go to stdout.
    """
    return logging.getLogger(name)


def setup_logging(level=DEFAULT_LEVEL,
                  log_file=LOG_FILE,
                  max_bytes=DEFAULT_MAX_BYTES,
                  backup_count=DEFAULT_BACKUP_COUNT,
                  enable_file_logging=FILE_LOGGING_ENABLED):
    """Configure root logging with a plain stdout handler.

    Idempotent: repeated calls do not attach duplicate handlers (the
    level is still updated). Pass ``enable_file_logging=True`` (or flip
    :const:`FILE_LOGGING_ENABLED`) to also write the decorated format to
    a rotating log file. Returns the log file path, or None when file
    logging is off.
    """
    global _configured

    root = logging.getLogger()
    root.setLevel(level)

    plain_formatter = logging.Formatter(CONSOLE_FORMAT)

    def _ensure_handler(handler, formatter):
        handler.setLevel(level)
        handler.setFormatter(formatter)
        root.addHandler(handler)

    if not _configured:
        _ensure_handler(logging.StreamHandler(sys.stdout), plain_formatter)
        if enable_file_logging:
            try:
                log_file = Path(log_file)
                log_file.parent.mkdir(parents=True, exist_ok=True)
                _ensure_handler(
                    RotatingFileHandler(
                        str(log_file),
                        maxBytes=max_bytes,
                        backupCount=backup_count,
                        encoding="utf-8",
                    ),
                    logging.Formatter(FILE_FORMAT),
                )
            except Exception as exc:
                root.warning("Could not create log file handler: %s", exc)
        _configured = True
    else:
        # Keep levels in sync without duplicating handlers.
        for handler in root.handlers:
            handler.setLevel(level)

    if enable_file_logging:
        try:
            return Path(log_file)
        except Exception:
            return None
    return None


def log_exception(logger, msg, *args):
    """Log *msg* with traceback for use in broad-except paths.

    Must be called from inside an ``except`` block; falls back to
    ``logger.error(..., exc_info=True)`` semantics via
    :meth:`logging.Logger.exception`.
    """
    if logger is None:
        logger = logging.getLogger()
    logger.exception(msg, *args)
