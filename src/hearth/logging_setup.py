"""Logging configuration.

Standard-library practice, applied strictly:

* every module does ``log = logging.getLogger(__name__)`` and nothing else;
* handlers, levels and formatting are configured exactly once, from the
  entry point, never at import time and never inside a library module;
* the package logger gets a ``NullHandler`` so importing Hearth from another
  program produces no output unless that program asks for it.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import sys

from hearth import paths

__all__ = ["DEFAULT_LEVEL", "LEVELS", "configure"]

LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
DEFAULT_LEVEL = "INFO"

_MAX_BYTES = 1_000_000
_BACKUPS = 3

# Import-time, and safe: attaching a NullHandler is the one thing a library is
# supposed to do. It sets no level and produces no output.
logging.getLogger("hearth").addHandler(logging.NullHandler())


def _resolve_level(level: str | None) -> int:
    name = (level or os.environ.get("HEARTH_LOG_LEVEL") or DEFAULT_LEVEL).upper()
    if name not in LEVELS:
        name = DEFAULT_LEVEL
    level_value = getattr(logging, name)
    return int(level_value)


def configure(level: str | None = None, *, to_file: bool = True) -> None:
    """Configure logging for the running process. Call once, from the CLI.

    Args:
        level: Explicit level name. Falls back to ``$HEARTH_LOG_LEVEL``, then
            to ``INFO``.
        to_file: Also write a rotating log to the XDG state directory. Turned
            off by tests and by anything running under systemd, where the
            journal already captures stderr.
    """
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)

    root.setLevel(_resolve_level(level))

    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    root.addHandler(console)

    if not to_file:
        return

    try:
        paths.state_dir().mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            paths.log_file(),
            maxBytes=_MAX_BYTES,
            backupCount=_BACKUPS,
            encoding="utf-8",
        )
    except OSError as exc:
        # A read-only or missing home is not a reason to refuse to start.
        root.warning("file logging disabled: %s", exc)
        return

    file_handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s")
    )
    root.addHandler(file_handler)
