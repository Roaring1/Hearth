"""Small smoke tests for the GTK4 layer; never starts the live mixer or audio pollers."""

from __future__ import annotations

import pytest


@pytest.mark.gui
def test_gtk4_mixer_import_and_application_identity() -> None:
    """Load the actual GTK4 entry module and verify desktop identity consistency."""
    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk

    from hearth import APP_ID
    from hearth.ui.gtk4 import app

    assert Gtk.get_major_version() == 4
    assert app.APP_ID == APP_ID
    application = Gtk.Application(application_id=APP_ID)
    assert application.get_application_id() == APP_ID
