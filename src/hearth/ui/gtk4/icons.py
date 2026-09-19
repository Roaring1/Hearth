"""Real application icons for the mixer's app marks.

PipeWire only tells us an application's *name* ("vesktop", "Firefox",
"gst-launch-1.0"), so this module has to work back from that to an icon.

There are two fetchers, tried in order, because neither is enough alone:

1. **Desktop entries plus the icon theme.** Covers everything installed the
   normal way, where ``Icon=`` names an icon the running theme carries.
2. **A direct scan of the icon directories.** Covers the rest. An icon can be
   perfectly present on disk and still be invisible to :class:`Gtk.IconTheme`:
   Flatpak exports, loose pixmaps, and - the case that started this - a theme
   such as Papirus that is installed but is not the theme GTK resolved to.
   ``vesktop.svg`` lives in the Papirus tree and nowhere else, so asking the
   theme for it fails while the file sits right there.

The letter chip remains the last resort, so an unknown app still renders.
"""

from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gtk  # noqa: E402

#: Extra names to try for apps whose PipeWire name will never match an entry.
#: Each value is tried in order, through both fetchers.
_ALIASES: dict[str, tuple[str, ...]] = {
    "vesktop": ("vesktop", "dev.vencord.Vesktop", "discord"),
    "webcord": ("webcord", "discord"),
    "sober": ("sober", "org.vinegarhq.Sober", "roblox"),
    "roblox": ("roblox", "org.vinegarhq.Sober", "grapejuice-roblox-studio"),
    "paplay": ("audio-x-generic",),
    "parec": ("audio-x-generic",),
    "speechdispatcher": ("audio-x-generic",),
    "gstlaunch": ("applications-multimedia",),
    "chromium": ("chromium", "chromium-browser"),
    "obs": ("com.obsproject.Studio", "obs"),
}

_IMAGE_SUFFIXES = (".svg", ".png", ".xpm")
_WORD = re.compile(r"[^a-z0-9]+")


def _key(value: str) -> str:
    """Normalise a name so "Roblox Studio" and "roblox-studio" agree."""
    return _WORD.sub("", (value or "").lower())


def _data_dirs() -> list[Path]:
    home = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share")
    raw = os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share"
    dirs = [home]
    dirs += [Path(part) for part in raw.split(":") if part]
    dirs.append(Path("/var/lib/flatpak/exports/share"))
    dirs.append(home / "flatpak/exports/share")
    seen: list[Path] = []
    for path in dirs:
        if path not in seen:
            seen.append(path)
    return seen


def _field(text: str, field: str) -> str:
    """First value of a desktop-entry key, ignoring localised variants."""
    match = re.search(rf"^{field}\s*=\s*(.+)$", text, re.MULTILINE)
    return match.group(1).strip() if match else ""


@lru_cache(maxsize=1)
def _entry_index() -> dict[str, str]:
    """Map normalised app names to ``Icon=`` values from desktop entries.

    Earlier directories win: the user's own entries in ``~/.local/share`` are
    the ones they actually launch things with.
    """
    index: dict[str, str] = {}
    for base in _data_dirs():
        try:
            paths = sorted((base / "applications").glob("*.desktop"))
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
            keys = [path.stem, path.stem.rsplit(".", 1)[-1], icon]
            keys.append(_field(text, "Name"))
            keys.append(_field(text, "StartupWMClass"))
            exec_line = _field(text, "Exec").split()
            if exec_line:
                keys.append(Path(exec_line[0]).name)
            for key in keys:
                norm = _key(key)
                if norm:
                    index.setdefault(norm, icon)
    return index


@lru_cache(maxsize=1)
def _file_index() -> dict[str, str]:
    """Map normalised icon-file stems to absolute paths.

    Scalable SVGs win outright and larger bitmaps beat smaller ones, so a mark
    never has to scale up from a 16px icon.
    """
    index: dict[str, str] = {}
    scored: dict[str, int] = {}
    roots = [Path("/usr/share/pixmaps")] + [base / "icons" for base in _data_dirs()]
    for root in roots:
        if not root.is_dir():
            continue
        try:
            entries = list(root.rglob("*"))
        except OSError:
            continue
        for path in entries:
            if path.suffix.lower() not in _IMAGE_SUFFIXES:
                continue
            norm = _key(path.stem)
            if not norm:
                continue
            score = _score(path)
            if score > scored.get(norm, -1):
                scored[norm] = score
                index[norm] = str(path)
    return index


def _score(path: Path) -> int:
    """Rank an icon file: scalable beats big, big beats small."""
    if path.suffix.lower() == ".svg":
        return 10_000
    match = re.search(r"(\d+)x\1", str(path))
    return int(match.group(1)) if match else 1


def _candidates(app: str) -> list[str]:
    """Names worth trying for this app, most specific first."""
    norm = _key(app)
    names: list[str] = [app]
    for alias, values in _ALIASES.items():
        if alias == norm or (len(alias) >= 4 and alias in norm):
            names.extend(values)
    first = re.split(r"[^A-Za-z0-9]+", app.strip())[:1]
    if first and first[0]:
        names.append(first[0])
    out: list[str] = []
    for name in names:
        if name and name not in out:
            out.append(name)
    return out


def _from_entries(name: str) -> str:
    """Icon name for ``name`` according to desktop entries, or ""."""
    index = _entry_index()
    norm = _key(name)
    if not norm:
        return ""
    if norm in index:
        return index[norm]
    for key, icon in index.items():
        if len(key) >= 4 and (key in norm or norm in key):
            return icon
    return ""


def _from_files(name: str) -> str:
    """Absolute path to an icon file for ``name``, or ""."""
    index = _file_index()
    norm = _key(name)
    if not norm:
        return ""
    if norm in index:
        return index[norm]
    for key, path in index.items():
        if len(key) >= 5 and (key in norm or norm in key):
            return path
    return ""


def _themed(name: str) -> bool:
    display = Gdk.Display.get_default()
    if display is None or not name or name.startswith("/"):
        return False
    return Gtk.IconTheme.get_for_display(display).has_icon(name)


def resolve(app: str) -> str:
    """An icon name or absolute file path for an app, or "" if there is none."""
    names = _candidates(app)
    for name in names:  # fetcher one: entries, then the theme's own names
        icon = _from_entries(name)
        if icon.startswith("/") or (icon and _themed(icon)):
            return icon
        if _themed(name):
            return name
    for name in names:  # fetcher two: the icon files themselves
        path = _from_files(name)
        if path:
            return path
        entry_icon = _from_entries(name)
        if entry_icon:
            path = _from_files(entry_icon)
            if path:
                return path
    return ""


def icon_image(app: str, size: int) -> Gtk.Image | None:
    """A themed or on-disk icon for this app, or None to use the letter chip."""
    icon = resolve(app)
    if not icon:
        return None
    if icon.startswith("/"):
        if not Path(icon).exists():
            return None
        image = Gtk.Image.new_from_file(icon)
    else:
        image = Gtk.Image.new_from_icon_name(icon)
    image.set_pixel_size(size)
    image.set_tooltip_text(app)
    return image
