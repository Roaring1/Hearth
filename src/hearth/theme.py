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
import os
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


WINDOWS_LIGHT: dict[str, str] = {
    "window-bg": "#f3f3f3",
    "window-bg-alt": "#e5e5e5",
    "window-fg": "#202020",
    "window-fg-dim": "#666666",
    "view-bg": "#ffffff",
    "view-bg-alt": "#f4f4f4",
    "view-fg": "#202020",
    "view-fg-dim": "#606060",
    "button-bg": "#e7e7e7",
    "button-fg": "#202020",
    "selection-bg": "#ad3376",
    "selection-fg": "#ffffff",
    "tooltip-bg": "#fffdf8",
    "tooltip-fg": "#202020",
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
    #: False when no usable desktop palette could be read.
    from_desktop: bool = False
    #: True when Windows asks apps to use the accent on native title bars.
    accent_titlebar: bool = False

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
        """The tokens as ``@define-color`` lines, to prepend to a stylesheet.

        Not ``:root { --token: ... }``. GTK's CSS engine is not a browser:
        it has no custom properties, and a rule it cannot parse makes it
        drop the *entire* stylesheet, so the window would come up unstyled
        rather than merely mis-coloured. ``@define-color`` is the GTK
        spelling and works in both GTK3 and GTK4; the colours are then
        referenced as ``@window-bg``.
        """
        return "".join(
            f"@define-color {name} {value};\n" for name, value in sorted(self.tokens.items())
        )


def parse_windows_accent_dword(value: int) -> str | None:
    """Decode DWM's ABGR DWORD into a CSS #rrggbb colour."""
    if not isinstance(value, int) or value < 0 or value > 0xFFFFFFFF:
        return None
    red = value & 0xFF
    green = (value >> 8) & 0xFF
    blue = (value >> 16) & 0xFF
    return f"#{red:02x}{green:02x}{blue:02x}"


def windows_theme_from_values(
    *,
    accent_dword: int | None,
    apps_use_light_theme: int | None,
    color_prevalence: int | None,
) -> Theme:
    """Build a Windows palette from the user's already-selected OS settings."""
    is_light = bool(apps_use_light_theme) if apps_use_light_theme is not None else False
    tokens = dict(WINDOWS_LIGHT if is_light else FALLBACK)
    accent = parse_windows_accent_dword(accent_dword) if accent_dword is not None else None
    if accent is not None:
        tokens["accent"] = accent
        tokens["selection-bg"] = accent
        tokens["selection-fg"] = "#ffffff" if luminance(accent) < 0.50 else "#171717"
    return Theme(
        tokens=tokens,
        icon_theme="windows",
        from_desktop=any(
            value is not None for value in (accent_dword, apps_use_light_theme, color_prevalence)
        ),
        accent_titlebar=bool(color_prevalence),
    )


def read_windows() -> Theme:
    """Read Windows accent and app light/dark preferences without importing GTK."""
    try:
        import winreg
    except ImportError:
        log.info("theme: Windows registry access is unavailable")
        return Theme()

    def value(key_path: str, name: str) -> int | None:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
                raw, kind = winreg.QueryValueEx(key, name)
        except OSError:
            return None
        if kind != winreg.REG_DWORD or not isinstance(raw, int):
            return None
        return raw

    dwm_key = r"Software\Microsoft\Windows\DWM"
    personalize_key = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
    explorer_accent_key = r"Software\Microsoft\Windows\CurrentVersion\Explorer\Accent"
    accent = value(dwm_key, "AccentColor")
    if accent is None:
        accent = value(explorer_accent_key, "AccentColorMenu")
    apps_light = value(personalize_key, "AppsUseLightTheme")
    if apps_light is None:
        apps_light = value(personalize_key, "SystemUsesLightTheme")
    prevalence = value(dwm_key, "ColorPrevalence")
    result = windows_theme_from_values(
        accent_dword=accent,
        apps_use_light_theme=apps_light,
        color_prevalence=prevalence,
    )
    if not result.from_desktop:
        log.info("theme: Windows has no readable accent or colour-mode preferences")
        return Theme()
    log.info(
        "theme: using Windows %s palette with %s accent",
        "light" if not result.is_dark else "dark",
        result.accent,
    )
    return result


def apply_windows_titlebar(window_title: str, desktop: Theme | None = None) -> bool:
    """Apply the Windows user's dark-caption and accent-titlebar preferences.

    GTK's CSS styles the client area, not the native DWM caption. This small
    best-effort bridge keeps the real Windows caption while matching the OS
    accent. Unsupported DWM attributes are ignored on older Windows builds.
    """
    if os.name != "nt":
        return False

    try:
        import ctypes
        from ctypes import wintypes

        current = desktop or read_windows()
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True)

        get_pid = user32.GetWindowThreadProcessId
        get_pid.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        get_pid.restype = wintypes.DWORD
        get_title = user32.GetWindowTextW
        get_title.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        get_title.restype = ctypes.c_int
        enum_windows = user32.EnumWindows
        enum_windows.argtypes = [ctypes.c_void_p, wintypes.LPARAM]
        enum_windows.restype = wintypes.BOOL

        target_pid = os.getpid()
        matches: list[int] = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)  # type: ignore[misc]
        def visit(hwnd: int, _data: int) -> bool:
            pid = wintypes.DWORD()
            get_pid(hwnd, ctypes.byref(pid))
            if pid.value == target_pid:
                title = ctypes.create_unicode_buffer(256)
                get_title(hwnd, title, len(title))
                if title.value == window_title:
                    matches.append(int(hwnd))
            return True

        enum_windows(visit, 0)
        if not matches:
            log.warning(
                "theme: native Windows titlebar not applied; no HWND titled %r for pid %s",
                window_title,
                target_pid,
            )
            return False

        set_attribute = dwmapi.DwmSetWindowAttribute
        set_attribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
        set_attribute.restype = ctypes.c_long

        dark_applied = False
        caption_applied = not current.accent_titlebar
        failures: list[str] = []
        dark = ctypes.c_int(1 if current.is_dark else 0)
        for hwnd in matches:
            # Attribute 20 is the documented Windows 11/modern Windows 10
            # value; 19 is retained as a compatibility fallback.
            dark_result = set_attribute(hwnd, 20, ctypes.byref(dark), ctypes.sizeof(dark))
            if dark_result < 0:
                dark_result = set_attribute(hwnd, 19, ctypes.byref(dark), ctypes.sizeof(dark))
            dark_applied = dark_applied or dark_result >= 0
            if dark_result < 0:
                failures.append(f"dark-mode HRESULT=0x{dark_result & 0xFFFFFFFF:08X}")

            if current.accent_titlebar:
                colour = current.accent
                if colour.startswith("#") and len(colour) == 7:
                    red, green, blue = (int(colour[i : i + 2], 16) for i in (1, 3, 5))
                    # DWM caption/text attributes use Win32 COLORREF: 0x00BBGGRR.
                    caption = ctypes.c_uint32(red | (green << 8) | (blue << 16))
                    text_colour = "#ffffff" if luminance(colour) < 0.50 else "#171717"
                    r, g, b = (int(text_colour[i : i + 2], 16) for i in (1, 3, 5))
                    caption_text = ctypes.c_uint32(r | (g << 8) | (b << 16))
                    caption_result = set_attribute(
                        hwnd, 35, ctypes.byref(caption), ctypes.sizeof(caption)
                    )
                    text_result = set_attribute(
                        hwnd, 36, ctypes.byref(caption_text), ctypes.sizeof(caption_text)
                    )
                    caption_applied = caption_applied or caption_result >= 0
                    if caption_result < 0:
                        failures.append(
                            f"accent-caption HRESULT=0x{caption_result & 0xFFFFFFFF:08X}"
                        )
                    if text_result < 0:
                        failures.append(f"caption-text HRESULT=0x{text_result & 0xFFFFFFFF:08X}")
                else:
                    failures.append(f"invalid accent colour {colour!r}")

        applied = dark_applied or caption_applied
        if applied:
            log.info(
                "theme: native Windows titlebar applied to %s HWND(s) (dark=%s, accent=%s)",
                len(matches),
                current.is_dark,
                current.accent if current.accent_titlebar else "system default",
            )
        if failures:
            log.warning(
                "theme: native Windows titlebar was only partially applied: %s",
                "; ".join(failures),
            )
        if not applied:
            log.warning("theme: DWM rejected all native Windows titlebar attributes")
        return applied
    except Exception:
        log.warning("theme: could not apply native Windows titlebar colours", exc_info=True)
        return False


def read(path: Path | None = None) -> Theme:
    """Read the desktop's palette, falling back rather than failing."""
    if path is None and os.name == "nt":
        return read_windows()
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
