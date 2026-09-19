#!/usr/bin/env python3
"""Install Hearth's Plasma-derived stylesheet over the shipped one.

What this does, and why it is shaped like this:

* It **transforms** the existing ``tokens.css`` instead of replacing it with a
  hand-written file. Every selector the UI relies on therefore survives by
  construction -- a rewrite can silently drop a class, a substitution cannot.
* It keeps a pristine copy (``tokens.css.orig``) the first time it runs, so
  running it twice is the same as running it once, and uninstalling is exact.
* It refuses to install a stylesheet that does not parse, or that has lost a
  class the Python code actually applies. Both are checked against the real
  sources, not against a list I typed out.
* GTK3 is the target. GTK3 CSS has no ``var()`` and no ``:root`` -- ``:root``
  is a hard parse error -- so colour tokens are emitted as ``@define-color``,
  which is the native GTK3 mechanism.

Usage:
    install_plasma_theme.py --check      inspect only, change nothing
    install_plasma_theme.py --dry-run    render and verify, but do not write
    install_plasma_theme.py              install (backs up, rolls back on failure)
    install_plasma_theme.py --uninstall  restore the pristine stylesheet
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path

DEFAULT_REPO = Path("/home/roaring/Documents/GitHub/Hearth")
REL_TOKENS = Path("src/hearth/ui/style/tokens.css")


# --------------------------------------------------------------------------
# palette
# --------------------------------------------------------------------------

# Used only when the desktop cannot be read. Mirrors hearth.theme.FALLBACK so
# the two cannot drift into disagreeing about what this rig looks like.
FALLBACK = {
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

# Meanings that are Hearth's own rather than the desktop's. One job each.
SEMANTIC = {
    "ok": "#30d158",  # running / signal present
    "bad": "#ff453a",  # fault, and nothing else
    "hot": "#e0c14a",  # a meter above -6 dB
    "unknown": "#c8a2e0",  # could not check -- never red
}


def load_palette(repo: Path) -> tuple[dict[str, str], str]:
    """Read the desktop palette through hearth.theme, or fall back.

    The repo root contains a ``hearth.py`` path-shim that shadows the real
    ``src/hearth`` package, so the import path is built deliberately: src
    first, and the repo root and CWD removed.
    """
    src = repo / "src"
    clean = [p for p in sys.path if p not in ("", str(repo), str(Path.cwd()))]
    sys.path[:] = [str(src), *clean]
    for name in list(sys.modules):
        if name == "hearth" or name.startswith("hearth."):
            del sys.modules[name]
    try:
        from hearth import theme  # type: ignore

        read = theme.read()
        tokens = dict(read.tokens)
        if not tokens:
            raise ValueError("theme returned no tokens")
        source = getattr(read, "from_desktop", None)
        origin = "kdeglobals" if source in (True, None) else "built-in fallback"
        merged = dict(FALLBACK)
        merged.update(tokens)
        return merged, f"hearth.theme ({origin})"
    except Exception as exc:
        return dict(FALLBACK), f"built-in fallback ({type(exc).__name__}: {exc})"


def mix(colour: str, other: str, amount: float) -> str:
    """Blend two #rrggbb colours. Used for hover states."""

    def parts(value: str) -> tuple[int, int, int]:
        value = value.lstrip("#")
        return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)

    a, b = parts(colour), parts(other)
    out = [round(x + (y - x) * amount) for x, y in zip(a, b, strict=True)]
    r, g, bl = (max(0, min(255, v)) for v in out)
    return f"#{r:02x}{g:02x}{bl:02x}"


# --------------------------------------------------------------------------
# the transform
# --------------------------------------------------------------------------

# Literal colours in the shipped stylesheet, mapped to a token name.
#
# The blue (#0a84ff and friends) was an invented accent; it becomes the
# desktop's own accent. The amber (#ff9f0a) was doing two jobs -- "warning"
# and "primary button" -- so the button becomes accent and only the warning
# keeps a warm colour.
SUBSTITUTIONS: list[tuple[str, str]] = [
    # window / surfaces
    ("#1c1c1e", "@h_window_bg"),
    ("#252527", "@h_window_bg_alt"),
    ("rgba(20,20,22,0.98)", "@h_view_bg"),
    ("rgba(16,16,18,0.96)", "@h_view_bg"),
    ("rgba(14,14,16,0.98)", "@h_view_bg"),
    ("rgba(18,18,20,0.99)", "@h_window_bg"),
    ("rgba(40,40,44,0.80)", "@h_view_bg_alt"),
    ("rgba(52,52,58,0.90)", "@h_surface_hi"),
    ("rgba(36,36,42,0.90)", "@h_view_bg_alt"),
    # accent: hardware / primary
    ("#0a84ff", "@h_accent"),
    ("#2a9aff", "@h_accent_hi"),
    ("#4db0ff", "@h_accent_hi"),
    ("#ddeeff", "@h_accent_hi"),
    ("rgba(10,132,255,0.16)", "alpha(@h_accent, 0.16)"),
    ("rgba(10,132,255,0.28)", "alpha(@h_accent, 0.28)"),
    ("rgba(10,132,255,0.30)", "alpha(@h_accent, 0.30)"),
    ("rgba(100,168,255,0.70)", "alpha(@h_accent, 0.75)"),
    ("rgba(8,44,84,0.60)", "alpha(@h_accent, 0.14)"),
    # amber: warning only
    ("#ff9f0a", "@h_hot"),
    ("#ffb340", "@h_hot_hi"),
    ("rgba(255,159,10,0.28)", "alpha(@h_hot, 0.28)"),
    ("rgba(255,159,10,0.60)", "alpha(@h_hot, 0.65)"),
    # fault
    ("#ff453a", "@h_bad"),
    ("rgba(255,69,58,0.78)", "alpha(@h_bad, 0.78)"),
    ("rgba(255,69,58,0.38)", "alpha(@h_bad, 0.38)"),
    ("rgba(255,69,58,0.18)", "alpha(@h_bad, 0.18)"),
    ("rgba(255,105,96,0.80)", "alpha(@h_bad, 0.85)"),
    ("rgba(72,12,12,0.70)", "alpha(@h_bad, 0.16)"),
    # signal
    ("#30d158", "@h_ok"),
    ("rgba(48,209,88,0.65)", "alpha(@h_ok, 0.65)"),
]

# The primary-action button was a filled amber block, which made every window
# with a button in it read as a warning. It becomes an accent button.
POST_FIXES: list[tuple[str, str]] = [
    (
        "background: @h_hot; color: @h_window_bg;",
        "background: @h_accent; color: @h_accent_fg;",
    ),
    ("background: @h_hot_hi;", "background: @h_accent_hi;"),
    # A fader that is merely sitting at a level has not been touched by
    # hardware. Straight substitution gave every fill full accent, which
    # left seven identical pink columns and no way to see which strip a
    # knob just moved. Resting fills use the dimmer selection colour;
    # accent stays reserved for interaction.
    (
        "scale highlight { background: @h_accent;",
        "scale highlight { background: alpha(@h_fg, 0.24);",
    ),
    (
        # accent means "this is the channel you are touching right now"
        "scale slider:hover { background: @h_accent_hi;",
        "scale:hover highlight, scale:active highlight { background: @h_accent; }\n"
        "scale slider:hover { background: @h_accent_hi;",
    ),
    # SSH is an ordinary action sitting among ordinary actions. It was only
    # tinted because the legacy sheet painted it with the invented blue.
    (
        "    background: alpha(@h_accent, 0.16);\n"
        "    border: 1px solid alpha(@h_accent, 0.28);\n"
        "    border-radius: 7px; padding: 4px 10px;\n"
        "    color: @h_accent_hi; font-size: 9px;",
        "    background: alpha(@h_fg, 0.06);\n"
        "    border: 1px solid alpha(@h_fg, 0.09);\n"
        "    border-radius: 7px; padding: 4px 10px;\n"
        "    color: alpha(@h_fg, 0.70); font-size: 9px;",
    ),
    (
        ".btn-ssh:hover { background: alpha(@h_accent, 0.30); }",
        ".btn-ssh:hover { background: alpha(@h_fg, 0.12); }",
    ),
]

HEADER_MARK = "/* hearth: plasma palette"


def build_header(palette: dict[str, str], origin: str) -> str:
    accent = palette["accent"]
    lines = [
        f"{HEADER_MARK} -- generated, do not edit by hand */",
        f"/* source: {origin} */",
        "/* GTK3 has no var() and treats :root as a parse error, so these are",
        "   @define-color, which is the GTK3-native mechanism. */",
        "",
        f"@define-color h_window_bg {palette['window-bg']};",
        f"@define-color h_window_bg_alt {palette['window-bg-alt']};",
        f"@define-color h_view_bg {palette['view-bg']};",
        f"@define-color h_view_bg_alt {palette['view-bg-alt']};",
        f"@define-color h_surface_hi {mix(palette['view-bg-alt'], '#ffffff', 0.08)};",
        f"@define-color h_fg {palette['window-fg']};",
        f"@define-color h_fg_dim {palette['window-fg-dim']};",
        f"@define-color h_selection {palette['selection-bg']};",
        "",
        "/* one meaning per colour */",
        f"@define-color h_accent {accent};            /* hardware touched it */",
        f"@define-color h_accent_hi {mix(accent, '#ffffff', 0.22)};",
        "@define-color h_accent_fg #ffffff;",
        f"@define-color h_ok {SEMANTIC['ok']};                /* running / signal */",
        f"@define-color h_bad {SEMANTIC['bad']};               /* fault, nothing else */",
        f"@define-color h_hot {SEMANTIC['hot']};               /* meter above -6 dB */",
        f"@define-color h_hot_hi {mix(SEMANTIC['hot'], '#ffffff', 0.25)};",
        f"@define-color h_unknown {SEMANTIC['unknown']};           /* could not check */",
        "",
    ]
    return "\n".join(lines)


def render(original: str, palette: dict[str, str], origin: str) -> str:
    body = original
    for needle, token in SUBSTITUTIONS:
        body = body.replace(needle, token)
    for needle, replacement in POST_FIXES:
        body = body.replace(needle, replacement)
    return build_header(palette, origin) + body


# --------------------------------------------------------------------------
# verification
# --------------------------------------------------------------------------

CLASS_RE = re.compile(r'add_class\("([^"]+)"')


def required_classes(repo: Path) -> set[str]:
    """Every CSS class the Python actually applies, read from the source."""
    found: set[str] = set()
    for path in (repo / "src").rglob("*.py"):
        found.update(CLASS_RE.findall(path.read_text(encoding="utf-8", errors="replace")))
    return found


def check_classes(css: str, needed: set[str]) -> list[str]:
    return sorted(name for name in needed if f".{name}" not in css)


def check_parses(css: str) -> list[str]:
    """Parse the stylesheet exactly the way the app will.

    Returns a list of problems; an empty list means GTK accepted every rule.
    If GTK is unavailable this cannot be verified, which is reported rather
    than assumed to be fine.
    """
    try:
        import gi  # noqa: TID251

        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk  # noqa: TID251
    except Exception as exc:
        return [f"could not verify: GTK3 unavailable ({exc})"]

    problems: list[str] = []
    provider = Gtk.CssProvider()
    provider.connect(
        "parsing-error",
        lambda _p, section, error: problems.append(
            f"line {section.get_start_line() + 1}: {error.message}"
        ),
    )
    try:
        provider.load_from_data(css.encode("utf-8"))
    except Exception as exc:
        problems.append(f"fatal: {exc}")
    return problems


def check_leftovers(css: str) -> list[str]:
    """Catch invented colours that escaped the substitution table."""
    banned = ["#0a84ff", "#2a9aff", "#4db0ff", "#ff9f0a", "#1c1c1e", "#252527"]
    return [colour for colour in banned if colour in css]


def verify(css: str, repo: Path) -> tuple[bool, list[str]]:
    report: list[str] = []
    ok = True

    needed = required_classes(repo)
    missing = check_classes(css, needed)
    if missing:
        ok = False
        report.append(f"FAIL  {len(missing)} class(es) lost: {', '.join(missing)}")
    else:
        report.append(f"ok    all {len(needed)} applied classes still styled")

    problems = check_parses(css)
    hard = [p for p in problems if not p.startswith("could not verify")]
    if hard:
        ok = False
        report.append(f"FAIL  GTK3 rejected {len(hard)} rule(s):")
        report.extend(f"      {p}" for p in hard[:10])
    elif problems:
        report.append(f"warn  {problems[0]}")
    else:
        report.append("ok    GTK3 parses the stylesheet with no errors")

    leftovers = check_leftovers(css)
    if leftovers:
        ok = False
        report.append(f"FAIL  invented colours remain: {', '.join(leftovers)}")
    else:
        report.append("ok    no hardcoded blue/amber/near-black left")

    return ok, report


# --------------------------------------------------------------------------
# install / uninstall
# --------------------------------------------------------------------------


def atomic_write(target: Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=str(target.parent), prefix=".tokens-", suffix=".css")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        shutil.copymode(target, tmp)
        Path(tmp).replace(target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=DEFAULT_REPO)
    parser.add_argument("--check", action="store_true", help="inspect only")
    parser.add_argument("--dry-run", action="store_true", help="verify without writing")
    parser.add_argument("--uninstall", action="store_true", help="restore the original")
    args = parser.parse_args()

    repo: Path = args.repo.expanduser()
    target = repo / REL_TOKENS
    pristine = target.with_suffix(".css.orig")

    if not target.is_file():
        print(f"error: no stylesheet at {target}", file=sys.stderr)
        return 2

    current = target.read_text(encoding="utf-8")
    installed = HEADER_MARK in current

    if args.uninstall:
        if not pristine.is_file():
            print("error: no pristine copy to restore", file=sys.stderr)
            return 2
        atomic_write(target, pristine.read_text(encoding="utf-8"))
        print(f"restored {target} from {pristine.name}")
        return 0

    if installed and not pristine.is_file():
        print(
            "error: stylesheet already generated but no pristine copy exists",
            file=sys.stderr,
        )
        return 2
    original = pristine.read_text(encoding="utf-8") if pristine.is_file() else current

    palette, origin = load_palette(repo)
    rendered = render(original, palette, origin)

    print(f"repo      {repo}")
    print(f"target    {target}")
    print(f"palette   {origin}")
    print(
        f"accent    {palette['accent']}   window {palette['window-bg']}   view {palette['view-bg']}"
    )
    state = "already installed -- re-rendering from pristine" if installed else "stock stylesheet"
    print(f"state     {state}")
    print()

    ok, report = verify(rendered, repo)
    for line in report:
        print(line)
    print()

    if args.check:
        return 0 if ok else 1
    if not ok:
        print("refusing to install: verification failed", file=sys.stderr)
        return 1
    if args.dry_run:
        print(f"dry run: would write {len(rendered)} bytes ({len(rendered.splitlines())} lines)")
        return 0

    if not pristine.is_file():
        shutil.copy2(target, pristine)
        print(f"kept pristine copy at {pristine.name}")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = target.with_name(f"tokens.css.bak-{stamp}")
    shutil.copy2(target, backup)
    print(f"backed up to {backup.name}")

    atomic_write(target, rendered)

    # Verify what actually landed on disk, not what we meant to write.
    landed = target.read_text(encoding="utf-8")
    ok_after, report_after = verify(landed, repo)
    if not ok_after:
        atomic_write(target, original)
        print("post-install verification FAILED -- rolled back", file=sys.stderr)
        for line in report_after:
            print(f"  {line}", file=sys.stderr)
        return 1

    print(f"installed {len(landed)} bytes; verified after writing")
    print("run with --uninstall to put the original back")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
