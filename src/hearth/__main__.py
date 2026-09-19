"""Entry point for ``python -m hearth``."""

from __future__ import annotations

import sys

from hearth.cli import main

if __name__ == "__main__":
    sys.exit(main())
