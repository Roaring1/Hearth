"""Small, hand-authored SVG action icons used by Hearth's GTK controls.

These are product UI glyphs, not generated illustrations or app-brand art.
Keep them monochrome and simple at 12-16 px; pair action icons with visible
text except for controls whose tooltip already names the action (e.g. minimise).
"""

from __future__ import annotations

from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

_ART = Path(__file__).with_name("art")
_ALLOWED = frozenset({"collapse", "refresh", "save", "sliders"})


def image(name: str, size: int = 12) -> Gtk.Image:
    """Load one bundled action SVG as a GTK image, failing loudly on bad assets."""
    if name not in _ALLOWED:
        raise ValueError(f"unknown Hearth action icon: {name}")
    path = _ART / f"{name}.svg"
    if not path.is_file():
        raise FileNotFoundError(f"Hearth action icon is missing: {path}")
    widget = Gtk.Image.new_from_file(str(path))
    widget.set_pixel_size(size)
    widget.set_tooltip_text(None)
    widget.set_can_target(False)
    widget.set_accessible_role(Gtk.AccessibleRole.PRESENTATION)
    return widget


def button(button: Gtk.Button, icon_name: str, label: str, size: int = 14) -> Gtk.Label:
    """Give a button a bundled SVG and visible label, preserving an AT name.

    Returns the child label so progress/success text can be changed without
    discarding the icon or relying on ``Gtk.Button.set_label`` after set_child.
    """
    row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
    glyph = image(icon_name, size)
    glyph.set_valign(Gtk.Align.CENTER)
    row.append(glyph)
    text = Gtk.Label(label=label)
    text.set_valign(Gtk.Align.CENTER)
    row.append(text)
    button.set_child(row)
    button.update_property([Gtk.AccessibleProperty.LABEL], [label])
    button.set_tooltip_text(label)
    return text


def accessible_label(button: Gtk.Button, label: str) -> None:
    """Keep a changing icon-button's assistive-technology name in sync."""
    button.update_property([Gtk.AccessibleProperty.LABEL], [label])


def set_label(button: Gtk.Button, text_widget: Gtk.Label | None, label: str) -> None:
    """Update a visible child label and accessible name, with a stub fallback.

    The fallback keeps small non-GTK test doubles useful for testing the save
    worker's threading/error behavior independently from the rendered widget.
    """
    if text_widget is None:
        button.set_label(label)
        return
    text_widget.set_text(label)
    button.update_property([Gtk.AccessibleProperty.LABEL], [label])
