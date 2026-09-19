#!/usr/bin/env python3
"""Compatibility shim.

``~/bin/hearth.py`` is a symlink to this file and the desktop entry used to
point at it, so it must keep working forever. Everything real now lives in the
``hearth`` package under ``src/``; this file only makes that package importable
when Hearth is run straight from a git checkout and hands over to the CLI.

Run the installed console script (``hearth``) or ``python -m hearth`` instead
when you have a choice.
"""

from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent / "src"
if (_SRC / "hearth" / "__init__.py").is_file():
    # Ahead of the script directory, or this very file shadows the package.
    sys.path.insert(0, str(_SRC))

from hearth.cli import main  # noqa: E402 - path shim must run first

if __name__ == "__main__":
    raise SystemExit(main())
