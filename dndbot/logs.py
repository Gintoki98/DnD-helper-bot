"""Log setup and helpers for reading recent errors.

Two files are written under ``logs/``:

* ``bot.log``   - everything, rotated at 5 MB with three backups kept.
* ``errors.log`` - errors and criticals only, small enough to open and read,
  which is what you actually want when something breaks at 2am.

``/errors`` reads the tail of ``errors.log`` so the current errors are
available from Telegram without shelling into the machine.
"""

from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path
from typing import Any

from . import config

LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)-22s %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_configured = False


class _MaxLevelFilter(logging.Filter):
    """Only let records at or above a level through (used for errors.log)."""

    def __init__(self, level: int) -> None:
        super().__init__()
        self.level = level

    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno >= self.level


def setup_logging(level: str | None = None) -> Path:
    """Configure console and file logging. Safe to call more than once."""
    global _configured
    if _configured:
        return config.LOG_FILE

    root = logging.getLogger()
    root.setLevel(getattr(logging, (level or config.LOG_LEVEL).upper(), logging.INFO))

    formatter = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    root.addHandler(console)

    main = logging.handlers.RotatingFileHandler(
        config.LOG_FILE,
        maxBytes=config.LOG_MAX_BYTES,
        backupCount=config.LOG_BACKUPS,
        encoding="utf-8",
    )
    main.setFormatter(formatter)
    root.addHandler(main)

    # A separate, unrotated-until-large file holding only errors.
    errors = logging.handlers.RotatingFileHandler(
        config.LOG_DIR / "errors.log",
        maxBytes=config.LOG_MAX_BYTES,
        backupCount=config.LOG_BACKUPS,
        encoding="utf-8",
    )
    errors.setFormatter(formatter)
    errors.setLevel(logging.ERROR)
    errors.addFilter(_MaxLevelFilter(logging.ERROR))
    root.addHandler(errors)

    # Telethon is chatty at INFO about reconnects; keep it to warnings.
    logging.getLogger("telethon").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)

    _configured = True
    return config.LOG_FILE


def errors_path() -> Path:
    return config.LOG_DIR / "errors.log"


def recent_errors(limit: int = 4000, max_entries: int = 12) -> list[str]:
    """Tail of the error log, split into readable entries.

    Returns a list of entry blocks, newest last. Multi-line tracebacks are kept
    with their entry but trimmed so one crash cannot flood the chat.
    """
    path = errors_path()
    if not path.exists():
        return []

    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            if size > limit * 4:
                handle.seek(size - limit * 4)
                handle.readline()  # discard the partial first line
            blob = handle.read().decode("utf-8", errors="replace")
    except OSError:
        return []

    entries: list[list[str]] = []
    for line in blob.splitlines():
        # A log line starts with a timestamp: "2026-09-30 18:16:13 ERROR ..."
        if len(line) > 19 and line[4] == "-" and line[10] == " " and line[13] == ":":
            entries.append([line])
        elif entries:
            entries[-1].append(line)
        else:
            entries.append([line])

    blocks = ["\n".join(lines) for lines in entries if lines]
    return blocks[-max_entries:]


def log_exc(message: str, **context: Any) -> None:
    """Log an error with its traceback plus any useful context."""
    log = logging.getLogger("dndbot")
    extra = " ".join(f"{key}={value!r}" for key, value in context.items())
    log.error("%s%s", message, f" [{extra}]" if extra else "", exc_info=True)
