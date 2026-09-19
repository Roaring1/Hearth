"""What the desktop already decided about colour, read without GTK.

Hearth lives on one person's Plasma desktop, where the window colour is a
near-black grey and the accent is a bright pink. A GTK app that ships its
own palette looks like a visitor. So instead of inventing colours, this
module reads the ones Plasma is already using and hands them over as CSS
custom properties for the stylesheet to consume.

Rules that keep this cheap and safe:

* Nothing here imports ``gi``. It is a file reader, so it can be tested
  without a display and called before a window exists.
* Dark or light is judged by how bright the window colour actually is,
  not by whether the scheme's name contains "dark". Names lie; this
  desktop's look-and-feel package is called WhiteSur-dark.
* A missing or unreadable ``kdeglobals`` is not an error. There is a
  documented fallback palette, and :attr:`Theme.from_desktop` says which
  one you got, so the UI can be honest instead of guessing.
* Only the handful of roles the UI needs are exposed. Plasma defines
  dozens; carrying all of them would be a second config format to keep
  in step.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from hearth import paths

log = logging.getLogger(__name__)

#: Which Plasma colour roles become which CSS custom property. The left
#: side is ``(section, key)`` in kdeglobals; the right is the token name.
ROLES: tuple[tuple[str, str, str], ...] = (
    ("Colors:Window", "BackgroundNormal", "window-bg"),
    ("Colors:Window", "BackgroundAlternate", "window-bg-alt"),
    ("Colors:Window", "ForegroundNormal", "window-fg"),
    ("Colors:Window", "ForegroundInactive", "window-fg-dim"),
    ("Colors:View", "BackgroundNormal", "view-bg"),
    ("Colors:View", "BackgroundAlternate", "view-bg-alt"),
    ("Colors:View", "ForegroundNormal", "view-fg"),
    ("Colors:View", "ForegroundInactive", "view-fg-dim"),
    ("Colors:Button", "BackgroundNormal", "button-bg"),
    ("Colors:Button", "ForegroundNormal", "button-fg"),
    ("Colors:Selection", "BackgroundNormal", "selection-bg"),
    ("Colors:Selection", "ForegroundNormal", "selection-fg"),
    ("Colors:Tooltip", "BackgroundNormal", "tooltip-bg"),
    ("Colors:Tooltip", "ForegroundNormal", "tooltip-fg"),
)

#: Used when Plasma cannot be read at all. Deliberately the dark, pink-accented
#: scheme this rig runs, so a fallback still looks deliberate rather than grey.
FALLBACK: dict[str, str] = {
    "window-bg": "#363636",
    "window-bg-alt": "#424242",
    "window-fg": "#fcfcfc",
    "window-fg-dim": "#a0a0a0",
    "view-bg": "#242424",
    "view-bg-alt": "#303030",
    "view-fg": "#fcfcfc",
    "view-fg-dim": "#a0a0a0",
    "button-bg": "#656565",
    "button-fg": "#fcfcfc",
    "selection-bg": "#ad3376",
    "selection-fg": "#ffffff",
    "tooltip-bg": "#333333",
    "tooltip-fg": "#fcfcfc",
    "accent": "#e93a9a",
}


def config_file() -> Path:
    return paths.config_home() / "kdeglobals"


def parse_ini(text: str) -> dict[str, dict[str, str]]:
    """Parse kdeglobals by hand.

    ``configparser`` chokes on this file: section names like
    ``[Colors:Header][Inactive]`` are not its grammar, and values contain
    unescaped ``%`` and ``:``. Reading it directly is both simpler and
    cheaper than teaching a parser the exceptions.
    """
    out: dict[str, dict[str, str]] = {}
    section = ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            out.setdefault(section, {})
            continue
        key, sep, value = line.partition("=")
        if not sep:
            continue
        out.setdefault(section, {})[key.strip()] = value.strip()
    return out


def parse_colour(value: str) -> str | None:
    """Turn Plasma's ``r,g,b`` or ``r,g,b,a`` into CSS.

    Returns ``#rrggbb`` normally, and ``rgba(...)`` when the colour is
    genuinely translucent, because flattening an alpha to opaque is the
    kind of quiet wrongness that only shows up as a misdrawn overlay.
    """
    parts = [p.strip() for p in value.split(",")]
    if len(parts) not in (3, 4):
        return None
    try:
        numbers = [int(p) for p in parts]
    except ValueError:
        return None
    if any(n < 0 or n > 255 for n in numbers):
        return None
    red, green, blue = numbers[:3]
    alpha = numbers[3] if len(numbers) == 4 else 255
    if alpha < 255:
        return f"rgba({red}, {green}, {blue}, {alpha / 255:.3f})"
    return f"#{red:02x}{green:02x}{blue:02x}"


def luminance(colour: str) -> float:
    """Perceived brightness of a ``#rrggbb`` token, 0.0 to 1.0.

    The usual sRGB weighting. Anything that is not a plain hex triple
    reports mid-grey, which decides nothing either way.
    """
    if not (colour.startswith("#") and len(colour) == 7):
        return 0.5
    try:
        red, green, blue = (int(colour[i : i + 2], 16) for i in (1, 3, 5))
    except ValueError:
        return 0.5
    return (0.2126 * red + 0.7152 * green + 0.0722 * blue) / 255.0


@dataclass(frozen=True, slots=True)
class Theme:
    """The desktop's colours, ready to become a stylesheet."""

    tokens: dict[str, str] = field(default_factory=lambda: dict(FALLBACK))
    icon_theme: str = "breeze"
    #: False when kdeglobals could not be read and FALLBACK is in use.
    from_desktop: bool = False

    @property
    def accent(self) -> str:
        return self.tokens.get("accent", FALLBACK["accent"])

    @property
    def background(self) -> str:
        return self.tokens.get("window-bg", FALLBACK["window-bg"])

    @property
    def is_dark(self) -> bool:
        """True when the window colour is darker than its own text."""
        return luminance(self.background) < 0.5

    def css(self) -> str:
        """The tokens as a ``:root`` block, ready to prepend to a stylesheet."""
        lines = [":root {"]
        lines += [f"  --{name}: {value};" for name, value in sorted(self.tokens.items())]
        lines.append("}")
        return "\n".join(lines) + "\n"


def read(path: Path | None = None) -> Theme:
    """Read the desktop's palette, falling back rather than failing."""
    target = path or config_file()
    try:
        text = target.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        log.info("theme: using built-in palette (%s)", exc)
        return Theme()

    config = parse_ini(text)
    tokens = dict(FALLBACK)
    found = 0
    for section, key, name in ROLES:
        colour = parse_colour(config.get(section, {}).get(key, ""))
        if colour:
            tokens[name] = colour
            found += 1

    general = config.get("General", {})
    accent = parse_colour(general.get("AccentColor", "")) or parse_colour(
        general.get("LastUsedCustomAccentColor", "")
    )
    if accent:
        tokens["accent"] = accent
        found += 1

    icon_theme = config.get("Icons", {}).get("Theme", "") or "breeze"
    if not found:
        # The file existed but said nothing about colour -- that is the
        # fallback case too, and saying so keeps `from_desktop` meaningful.
        log.info("theme: %s has no colour scheme, using built-in palette", target)
        return Theme(icon_theme=icon_theme)
    return Theme(tokens=tokens, icon_theme=icon_theme, from_desktop=True)


def describe(theme: Theme | None = None) -> str:
    """One line about where the look came from, for ``--status`` and logs."""
    current = theme or read()
    shade = "dark" if current.is_dark else "light"
    if not current.from_desktop:
        return f"Using Hearth's own {shade} colours"
    return f"Matching your desktop's {shade} colours and {current.accent} accent"
