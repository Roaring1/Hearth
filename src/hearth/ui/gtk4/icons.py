"""Real application icons for the mixer's app marks.

PipeWire only tells us an application's *name* ("vesktop", "Firefox",
"gst-launch-1.0"). Desktop entries are what map a name to an installed icon,
so this module builds one small index of them and answers lookups from it.

The letter chip stays as the fallback: a name with no desktop entry, or an
icon the current theme does not carry, still has to render as something.
"""

from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gtk  # noqa: E402

#: Names PipeWire reports that no desktop entry will ever match, mapped to
#: icons that are present on essentially every theme.
_ALIASES = {
    "vesktop": "discord",
    "webcord": "discord",
    "paplay": "audio-x-generic",
    "parec": "audio-x-generic",
    "speech-dispatcher": "audio-x-generic",
    "gst-launch-1.0": "applications-multimedia",
    "chromium": "chromium-browser",
    "obs": "com.obsproject.Studio",
}

_WORD = re.compile(r"[^a-z0-9]+")


def _key(value: str) -> str:
    """Normalise a name so "Roblox Studio" and "roblox-studio" agree."""
    return _WORD.sub("", (value or "").lower())


def _entry_dirs() -> list[Path]:
    home = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share")
    dirs = [home / "applications"]
    raw = os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share"
    dirs += [Path(part) / "applications" for part in raw.split(":") if part]
    dirs.append(Path("/var/lib/flatpak/exports/share/applications"))
    dirs.append(home / "flatpak/exports/share/applications")
    return dirs


@lru_cache(maxsize=1)
def _index() -> dict[str, str]:
    """Map normalised app names to icon names, from installed desktop entries.

    Later directories must not clobber earlier ones: the user's own entries in
    ``~/.local/share`` are the ones they actually launch things with.
    """
    index: dict[str, str] = {}
    for directory in _entry_dirs():
        try:
            paths = sorted(directory.glob("*.desktop"))
        except OSError:
            continue
        for path in paths:
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            icon = _field(text, "Icon")
            if not icon:
                continue
            keys = [path.stem, path.stem.rsplit(".", 1)[-1]]
            keys.append(_field(text, "Name"))
            keys.append(_field(text, "StartupWMClass"))
            exec_line = _field(text, "Exec")
            if exec_line:
                keys.append(Path(exec_line.split()[0]).name if exec_line.split() else "")
            for key in keys:
                norm = _key(key)
                if norm:
                    index.setdefault(norm, icon)
    return index


def _field(text: str, field: str) -> str:
    """First value of a desktop-entry key, ignoring localised variants."""
    match = re.search(rf"^{field}\s*=\s*(.+)$", text, re.MULTILINE)
    return match.group(1).strip() if match else ""


def icon_name(app: str) -> str:
    """Best icon name for an application name, or "" when nothing matches."""
    norm = _key(app)
    if not norm:
        return ""
    for alias, icon in _ALIASES.items():
        if _key(alias) == norm or _key(alias) in norm:
            return icon
    index = _index()
    if norm in index:
        return index[norm]
    # "Roblox Studio" should still find a "roblox" entry, and vice versa.
    for key, icon in index.items():
        if len(key) >= 4 and (key in norm or norm in key):
            return icon
    return ""


def icon_image(app: str, size: int) -> Gtk.Image | None:
    """A themed icon for this app at ``size`` px, or None to use the chip."""
    name = icon_name(app)
    if not name:
        return None
    if name.startswith("/"):
        path = Path(name)
        if not path.exists():
            return None
        image = Gtk.Image.new_from_file(name)
    else:
        display = Gdk.Display.get_default()
        if display is None:
            return None
        theme = Gtk.IconTheme.get_for_display(display)
        if not theme.has_icon(name):
            return None
        image = Gtk.Image.new_from_icon_name(name)
    image.set_pixel_size(size)
    image.set_tooltip_text(app)
    return image
