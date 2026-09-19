#!/usr/bin/env python3
"""Draw Hearth's own art: the Crucible cover and the strip icon set.

Drawn in code rather than downloaded so the art can be regenerated at any
size, and so the shapes come from the same palette the window does. Every
icon here is a *silhouette*: it has to stay legible at 12 px inside a strip,
which rules out detail, gradients and outlines thinner than a pixel.

Run::

    python3 scripts/draw_art.py            # writes into assets/art
    python3 scripts/draw_art.py /tmp/out   # or anywhere else
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import cairo

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "assets" / "art"

# The window palette. Kept literal here because art is not themed: a cover
# that changed colour with the desktop would not be a cover.
INK = (0.106, 0.106, 0.106)
BONE = (0.965, 0.965, 0.965)
EMBER = (0.914, 0.227, 0.604)
COAL = (0.141, 0.141, 0.141)
ASH = (0.627, 0.627, 0.627)


def _rgb(cr: cairo.Context, colour: tuple[float, float, float], alpha: float = 1.0) -> None:
    cr.set_source_rgba(*colour, alpha)


def crucible_cover(path: Path, size: int = 1024) -> None:
    """A crucible seen head on: a vessel, a pour, and the heat inside it.

    The forms are all circles and straight lines so the cover survives being
    scaled down to a 64 px tile in a file manager.
    """
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surface)
    s = size / 1024.0

    _rgb(cr, INK)
    cr.paint()

    # Heat bloom behind the vessel.
    bloom = cairo.RadialGradient(512 * s, 600 * s, 40 * s, 512 * s, 600 * s, 460 * s)
    bloom.add_color_stop_rgba(0.0, *EMBER, 0.55)
    bloom.add_color_stop_rgba(0.55, *EMBER, 0.12)
    bloom.add_color_stop_rgba(1.0, *EMBER, 0.0)
    cr.set_source(bloom)
    cr.paint()

    # The vessel: a truncated cone with a lip, drawn as one path.
    cr.move_to(300 * s, 380 * s)
    cr.line_to(724 * s, 380 * s)
    cr.line_to(648 * s, 792 * s)
    cr.curve_to(640 * s, 836 * s, 384 * s, 836 * s, 376 * s, 792 * s)
    cr.close_path()
    _rgb(cr, COAL)
    cr.fill_preserve()
    _rgb(cr, BONE)
    cr.set_line_width(14 * s)
    cr.stroke()

    # The melt: an ellipse of ember sitting in the mouth.
    cr.save()
    cr.translate(512 * s, 384 * s)
    cr.scale(1.0, 0.24)
    cr.arc(0, 0, 196 * s, 0, math.tau)
    cr.restore()
    _rgb(cr, EMBER)
    cr.fill()

    # The pour: a thin ribbon leaving the lip, because a crucible that never
    # pours is just a cup.
    cr.move_to(706 * s, 392 * s)
    cr.curve_to(790 * s, 470 * s, 812 * s, 620 * s, 790 * s, 880 * s)
    cr.line_to(742 * s, 880 * s)
    cr.curve_to(764 * s, 630 * s, 744 * s, 486 * s, 676 * s, 414 * s)
    cr.close_path()
    _rgb(cr, EMBER)
    cr.fill()

    # Three sparks, set off the centre line so the composition is not mirrored.
    for cx, cy, r, alpha in (
        (352, 268, 13, 0.9),
        (604, 214, 9, 0.7),
        (452, 172, 6, 0.5),
    ):
        cr.arc(cx * s, cy * s, r * s, 0, math.tau)
        _rgb(cr, EMBER, alpha)
        cr.fill()

    surface.write_to_png(str(path))


def _icon(path: Path, draw, size: int = 128) -> None:
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surface)
    cr.scale(size / 128.0, size / 128.0)
    _rgb(cr, BONE)
    draw(cr)
    surface.write_to_png(str(path))


def _speaker_body(cr: cairo.Context) -> None:
    cr.move_to(20, 50)
    cr.line_to(44, 50)
    cr.line_to(72, 24)
    cr.line_to(72, 104)
    cr.line_to(44, 78)
    cr.line_to(20, 78)
    cr.close_path()
    cr.fill()


def icon_unmuted(cr: cairo.Context) -> None:
    """Speaker with two waves: this app is being heard."""
    _speaker_body(cr)
    cr.set_line_width(8)
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    for radius in (18, 32):
        cr.arc(78, 64, radius, -0.9, 0.9)
        cr.stroke()


def icon_muted(cr: cairo.Context) -> None:
    """Speaker with a cross: the checkbox never said which state was which."""
    _speaker_body(cr)
    cr.set_line_width(9)
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.move_to(86, 46)
    cr.line_to(116, 82)
    cr.stroke()
    cr.move_to(116, 46)
    cr.line_to(86, 82)
    cr.stroke()


def icon_mic(cr: cairo.Context) -> None:
    """A capsule on a yoke: the B1 bus, not a headset boom."""
    cr.set_line_width(9)
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.move_to(52, 30)
    cr.curve_to(52, 16, 76, 16, 76, 30)
    cr.line_to(76, 62)
    cr.curve_to(76, 76, 52, 76, 52, 62)
    cr.close_path()
    cr.fill()
    cr.arc(64, 62, 26, 0, math.pi)
    cr.stroke()
    cr.move_to(64, 88)
    cr.line_to(64, 108)
    cr.stroke()


def icon_headset(cr: cairo.Context) -> None:
    """The A50: band plus two cups."""
    cr.set_line_width(10)
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.arc(64, 62, 38, math.pi, math.tau)
    cr.stroke()
    for x in (26, 102):
        cr.rectangle(x - 10, 58, 20, 36)
        cr.fill()


def icon_laptop(cr: cairo.Context) -> None:
    """A lid and a deck: the guest input."""
    cr.set_line_width(8)
    cr.rectangle(30, 30, 68, 46)
    cr.stroke()
    cr.move_to(16, 92)
    cr.line_to(112, 92)
    cr.line_to(102, 80)
    cr.line_to(26, 80)
    cr.close_path()
    cr.fill()


def icon_share(cr: cairo.Context) -> None:
    """One source, two destinations: the share bus."""
    cr.set_line_width(9)
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.move_to(34, 64)
    cr.line_to(94, 34)
    cr.stroke()
    cr.move_to(34, 64)
    cr.line_to(94, 94)
    cr.stroke()
    for cx, cy in ((34, 64), (94, 34), (94, 94)):
        cr.arc(cx, cy, 13, 0, math.tau)
        cr.fill()


def icon_unknown(cr: cairo.Context) -> None:
    """A dashed square: an app we could not identify, never a blank tile."""
    _rgb(cr, ASH)
    cr.set_line_width(8)
    cr.set_dash([12, 10])
    cr.rectangle(24, 24, 80, 80)
    cr.stroke()


ICONS = {
    "app-unmuted": icon_unmuted,
    "app-muted": icon_muted,
    "mic": icon_mic,
    "headset": icon_headset,
    "laptop": icon_laptop,
    "share": icon_share,
    "unknown": icon_unknown,
}


def main(argv: list[str]) -> int:
    out = Path(argv[1]) if len(argv) > 1 else OUT
    out.mkdir(parents=True, exist_ok=True)
    crucible_cover(out / "crucible-cover.png")
    for name, draw in ICONS.items():
        _icon(out / f"{name}.png", draw)
        _icon(out / f"{name}@2x.png", draw, size=256)
    print(f"wrote {1 + 2 * len(ICONS)} files to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
