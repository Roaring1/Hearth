"""Cairo widgets for the GTK4 mixer.

The round-10 design is built from four drawn things no stock widget provides:
a segmented stereo LED meter with peak hold, a slotted fader with a grooved
cap, one dB ruler per group, and a minimised channel sliver. They live here so
the layout code in :mod:`hearth.ui.gtk4.app` stays readable.

Nothing here talks to PipeWire. Levels are fed in, value changes are emitted
out, which keeps the widgets testable and the audio code toolkit-free.
"""

from __future__ import annotations

import math
import time
from itertools import pairwise
from typing import ClassVar

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import GObject, Gtk  # noqa: E402 - must follow require_version

__all__ = ["VMAX", "Fader", "Meter", "Scale", "Sliver", "parse_rgb"]

#: Faders run to 150 %, matching pactl and what the GTK3 window allowed.
VMAX = 150

#: Where a double click puts a fader: unity, not silence and not the top.
DEFAULT_VOLUME = 100

#: A 3 px lamp with a 1 px gap reads as a ladder rather than a bar.
_SEG_H = 3.0
_SEG_GAP = 1.0

#: Meter banding. Amber has exactly one meaning in this UI and this is it.
_BAND_HOT = 0.74
_BAND_CLIP = 0.90

_PEAK_HOLD_S = 1.4
_PEAK_FALL = 0.55  # fraction per second once the hold expires

#: Where the printed ruler puts each dB mark, bottom of the meter to top.
#: The poller reports linear RMS, so a linear bar would keep every normal
#: listening level squashed into the bottom two lamps and disagree with the
#: numbers drawn beside it. These are the same anchors :class:`Scale` prints.
_DB_ANCHORS = ((-96.0, 0.0), (-60.0, 0.04), (-42.0, 0.18), (-18.0, 0.48), (-6.0, 0.72), (0.0, 0.94))


def frac_from_level(level: float) -> float:
    """Linear 0.0-1.0 RMS to meter fill, following the printed dB ruler."""
    if level <= 1e-7:
        return 0.0
    db = 20.0 * math.log10(min(float(level), 1.0))
    if db >= 0.0:
        return 1.0
    for (lo_db, lo_f), (hi_db, hi_f) in pairwise(_DB_ANCHORS):
        if lo_db <= db <= hi_db:
            span = hi_db - lo_db
            return lo_f + (hi_f - lo_f) * ((db - lo_db) / span if span else 0.0)
    return 0.0


def parse_rgb(colour: str) -> tuple[float, float, float]:
    """``"#rrggbb"`` to a cairo triple. Unusable input reads as mid grey."""
    text = colour.strip().lstrip("#")
    if len(text) == 3:
        text = "".join(ch * 2 for ch in text)
    if len(text) != 6:
        return (0.5, 0.5, 0.5)
    try:
        return (
            int(text[0:2], 16) / 255.0,
            int(text[2:4], 16) / 255.0,
            int(text[4:6], 16) / 255.0,
        )
    except ValueError:
        return (0.5, 0.5, 0.5)


def rounded(cr, x: float, y: float, w: float, h: float, r: float) -> None:
    """Path a rounded rectangle. Callers fill or stroke it themselves."""
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 1.5 * math.pi)
    cr.close_path()


class Meter(Gtk.DrawingArea):
    """Stereo segmented level meter with a peak-hold tick.

    Both columns are fed one figure today because the peak poller reports one
    per source. Drawing two anyway costs nothing and means real per-channel
    data can arrive later without a layout change.
    """

    def __init__(self, palette: dict[str, str], width: int = 15, height: int = 76) -> None:
        super().__init__()
        self._pal = palette
        self._level = 0.0
        self._shown = 0.0
        self._peak = 0.0
        self._peak_at = 0.0
        self._muted = False
        self._dead = False
        self.set_content_width(width)
        self.set_content_height(height)
        self.set_draw_func(self._draw)

    def set_state(self, *, muted: bool, dead: bool) -> None:
        if (muted, dead) != (self._muted, self._dead):
            self._muted, self._dead = muted, dead
            self.queue_draw()

    def feed(self, level: float) -> None:
        """Accepts the poller's linear RMS; the dB mapping happens here."""
        self._level = frac_from_level(level)

    def tick(self) -> None:
        """Advance smoothing and peak decay by one frame, then repaint."""
        target = 0.0 if (self._muted or self._dead) else self._level
        self._shown += (target - self._shown) * 0.35
        if self._shown < 0.002:
            self._shown = 0.0
        now = time.monotonic()
        if self._shown >= self._peak:
            self._peak = self._shown
            self._peak_at = now
        elif now - self._peak_at > _PEAK_HOLD_S:
            self._peak = max(0.0, self._peak - _PEAK_FALL / 60.0)
        self.queue_draw()

    def _draw(self, _area: Gtk.DrawingArea, cr, w: int, h: int) -> None:
        trough = parse_rgb(self._pal["meter-dim"])
        cr.set_source_rgb(*parse_rgb(self._pal["meter-off"]))
        rounded(cr, 0, 0, w, h, 2)
        cr.fill()
        gap = 2.0
        cw = (w - 2.0 - gap) / 2
        count = max(5, int(h // (_SEG_H + _SEG_GAP)))
        for col in range(2):
            x = 1.0 + col * (cw + gap)
            for i in range(count):
                frac = (i + 1) / count
                y = h - (i + 1) * (_SEG_H + _SEG_GAP)
                lit = (not self._muted) and (not self._dead) and frac <= self._shown
                if lit:
                    band = "bad" if frac > _BAND_CLIP else ("hot" if frac > _BAND_HOT else "ok")
                    cr.set_source_rgb(*parse_rgb(self._pal[band]))
                else:
                    cr.set_source_rgb(*trough)
                cr.rectangle(x, y, cw, _SEG_H)
                cr.fill()
        if self._peak > 0.01 and not self._muted and not self._dead:
            y = h - self._peak * h
            cr.set_source_rgb(*parse_rgb(self._pal["fg"]))
            cr.rectangle(0, max(0.0, y - 1), w, 1.5)
            cr.fill()


class Fader(Gtk.DrawingArea):
    """A vertical slot with a grooved cap. Emits ``moved`` when dragged.

    Click, drag and scroll all set the value; double-click returns to 100 %.
    While a hardware control is driving it the cap takes the desktop accent,
    which is what makes MIDI feedback legible from across the room.
    """

    __gsignals__: ClassVar[dict] = {"moved": (GObject.SignalFlags.RUN_FIRST, None, (int,))}

    def __init__(self, palette: dict[str, str], width: int = 20, height: int = 76) -> None:
        super().__init__()
        self._pal = palette
        self._value = 0
        self._hw = False
        self._dead = False
        self.set_content_width(width)
        self.set_content_height(height)
        self.set_draw_func(self._draw)

        click = Gtk.GestureClick()
        click.set_button(1)
        # The drag gesture claims the pointer the moment a press lands, which
        # swallowed the second press and left double-click-to-reset dead.
        # Seeing presses during capture keeps the click count intact.
        click.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        click.connect("pressed", self._on_press)
        self.add_controller(click)
        drag = Gtk.GestureDrag()
        drag.set_button(1)
        drag.connect("drag-update", self._on_drag)
        self.add_controller(drag)
        scroll = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.VERTICAL)
        scroll.connect("scroll", self._on_scroll)
        self.add_controller(scroll)

    @property
    def value(self) -> int:
        return self._value

    def set_value(self, value: int) -> None:
        value = max(0, min(VMAX, int(value)))
        if value != self._value:
            self._value = value
            self.queue_draw()

    def set_hardware(self, touched: bool) -> None:
        if touched != self._hw:
            self._hw = touched
            self.queue_draw()

    def set_dead(self, dead: bool) -> None:
        if dead != self._dead:
            self._dead = dead
            self.queue_draw()

    def _emit_from_y(self, y: float) -> None:
        if self._dead:
            return
        h = max(1, self.get_height())
        frac = 1.0 - max(0.0, min(1.0, y / h))
        self.set_value(round(frac * VMAX))
        self.emit("moved", self._value)

    def _on_press(self, _g: Gtk.GestureClick, n_press: int, _x: float, y: float) -> None:
        if self._dead:
            return
        if n_press >= 2:
            self.set_value(DEFAULT_VOLUME)
            self.emit("moved", DEFAULT_VOLUME)
            return
        self._emit_from_y(y)

    def _on_drag(self, gesture: Gtk.GestureDrag, _dx: float, dy: float) -> None:
        # A hand never holds perfectly still through a double click, so a
        # pixel or two of travel must not count as a drag and overwrite the
        # reset the second press just made.
        if abs(dy) < 3.0:
            return
        ok, _sx, sy = gesture.get_start_point()
        if ok:
            self._emit_from_y(sy + dy)

    def _on_scroll(self, _c: Gtk.EventControllerScroll, _dx: float, dy: float) -> bool:
        self.set_value(self._value + (-2 if dy > 0 else 2))
        self.emit("moved", self._value)
        return True

    def _draw(self, _area: Gtk.DrawingArea, cr, w: int, h: int) -> None:
        # The round-10 fader is a wide dark travel track, not a hairline slot:
        # the cap has to read as a physical thing you can grab from across
        # the room, the same way the hardware fader does.
        track_w = max(10.0, w - 2.0)
        track_x = (w - track_w) / 2
        cr.set_source_rgb(*parse_rgb(self._pal["slot"]))
        rounded(cr, track_x, 0, track_w, h, 2)
        cr.fill()
        if self._dead:
            return
        frac = self._value / VMAX
        cap_h = 9.0
        y = (h - cap_h) - frac * (h - cap_h)
        cr.set_source_rgb(*parse_rgb(self._pal["accent" if self._hw else "cap"]))
        rounded(cr, track_x + 0.5, y, track_w - 1, cap_h, 2)
        cr.fill()
        cr.set_source_rgb(*parse_rgb(self._pal["cap-groove"]))
        cr.rectangle(track_x + 2.5, y + cap_h / 2 - 0.5, track_w - 5, 1.0)
        cr.fill()


class Scale(Gtk.DrawingArea):
    """A strip's dB ruler, drawn beside that strip's own meter."""

    MARKS = ((0.94, "0"), (0.72, "-6"), (0.48, "-18"), (0.18, "-42"))

    def __init__(self, palette: dict[str, str], height: int) -> None:
        super().__init__()
        self._pal = palette
        self.set_content_width(19)
        self.set_content_height(height)
        self.set_draw_func(self._draw)

    def set_meter_height(self, height: int) -> None:
        self.set_content_height(height)
        self.queue_draw()

    def _draw(self, _area: Gtk.DrawingArea, cr, w: int, h: int) -> None:
        cr.select_font_face("Andale Mono")
        cr.set_font_size(8.5)
        for frac, text in self.MARKS:
            y = h - frac * h
            cr.set_source_rgb(*parse_rgb(self._pal["rule"]))
            cr.rectangle(0, y, 4, 1)
            cr.fill()
            cr.set_source_rgb(*parse_rgb(self._pal["fg-dim"]))
            cr.move_to(6, y + 3.5)
            cr.show_text(text)


class Sliver(Gtk.DrawingArea):
    """A minimised channel in the right rail: name on its side, one state dot."""

    def __init__(
        self,
        palette: dict[str, str],
        name: str,
        length: int,
        horizontal: bool = False,
    ) -> None:
        super().__init__()
        self._pal = palette
        self._name = name
        self._level = 0.0
        self._muted = False
        self._horizontal = horizontal
        # ``length`` is the long axis either way: a rail along the bottom of
        # the window wants wide, short slivers, not rotated tall ones.
        self.set_content_width(length if horizontal else 24)
        self.set_content_height(24 if horizontal else length)
        self.set_draw_func(self._draw)

    def feed(self, level: float, muted: bool) -> None:
        self._level, self._muted = level, muted
        self.queue_draw()

    def _draw(self, _area: Gtk.DrawingArea, cr, w: int, h: int) -> None:
        cr.set_source_rgb(*parse_rgb(self._pal["view-alt"]))
        rounded(cr, 0, 0, w, h, 3)
        cr.fill()
        if self._muted:
            dot = self._pal["bad"]
        elif self._level > 0.02:
            dot = self._pal["ok"]
        else:
            dot = self._pal["fg-dim"]
        cr.set_source_rgb(*parse_rgb(dot))
        if self._horizontal:
            cr.arc(9, h / 2, 3, 0, 2 * math.pi)
        else:
            cr.arc(w / 2, 9, 3, 0, 2 * math.pi)
        cr.fill()
        cr.save()
        cr.set_source_rgb(*parse_rgb(self._pal["fg-dim"]))
        cr.select_font_face("Trebuchet MS")
        cr.set_font_size(9.5)
        if self._horizontal:
            cr.translate(18, h / 2 + 3.5)
        else:
            cr.translate(w / 2 + 3.5, h - 12)
            cr.rotate(-math.pi / 2)
        cr.show_text(self._name)
        cr.restore()
