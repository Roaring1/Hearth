"""GTK layer. The only package in Hearth allowed to import ``gi``.

There were two windows here: a GTK3 control panel and the GTK4 mixer. The
GTK3 one is gone. Everything in it that earned its keep was folded into the
mixer and its Setup window over rounds 19-23 -- unit health with per-fault
restarts, the A50 chat/game routing, the LPD8 map, the mic signal flow and
the Moonlight source -- and what remained was either duplicated by a strip
or was chrome nobody used.

``run_app`` therefore means the mixer, and importing this package no longer
drags in ``hearth.legacy_core``.
"""

from __future__ import annotations

__all__ = ["run_app"]


def run_app() -> int:
    """Run the mixer.

    The GTK4 module is imported here, not at package scope, so that merely
    naming ``hearth.ui`` does not pull in ``gi``.
    """
    from hearth.ui.gtk4.app import main

    return main([])
