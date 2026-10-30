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

from gi.repository import Gdk, GObject, Gtk  # noqa: E402 - must follow require_version

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
        self._accessible_percent = 0
        self.set_accessible_role(Gtk.AccessibleRole.METER)
        self.update_property(
            [
                Gtk.AccessibleProperty.VALUE_MIN,
                Gtk.AccessibleProperty.VALUE_MAX,
                Gtk.AccessibleProperty.VALUE_NOW,
                Gtk.AccessibleProperty.VALUE_TEXT,
            ],
            [0.0, 100.0, 0.0, "0%"],
        )
        self.set_content_width(width)
        self.set_content_height(height)
        self.set_draw_func(self._draw)

    def set_state(self, *, muted: bool, dead: bool) -> None:
        if (muted, dead) != (self._muted, self._dead):
            self._muted, self._dead = muted, dead
            # The drawing goes dark immediately when muted or unavailable.
            # Keep its accessible value truthful instead of announcing the
            # old smoothed level while the hidden visual buffer decays.
            if (muted or dead) and self._accessible_percent != 0:
                self._accessible_percent = 0
                self.update_property(
                    [Gtk.AccessibleProperty.VALUE_NOW, Gtk.AccessibleProperty.VALUE_TEXT],
                    [0.0, "0%"],
                )
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
        # A live meter changes every frame; round its accessible value to 5%
        # increments so screen readers get useful level changes without a
        # burst of announcements at the animation rate.
        accessible_percent = (
            0 if self._muted or self._dead else max(0, min(100, int(round(self._shown * 20) * 5)))
        )
        if accessible_percent != self._accessible_percent:
            self._accessible_percent = accessible_percent
            self.update_property(
                [Gtk.AccessibleProperty.VALUE_NOW, Gtk.AccessibleProperty.VALUE_TEXT],
                [float(accessible_percent), f"{accessible_percent}%"],
            )
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
        self.set_focusable(True)
        self.set_accessible_role(Gtk.AccessibleRole.SLIDER)
        self.update_property(
            [
                Gtk.AccessibleProperty.VALUE_MIN,
                Gtk.AccessibleProperty.VALUE_MAX,
                Gtk.AccessibleProperty.VALUE_NOW,
                Gtk.AccessibleProperty.VALUE_TEXT,
            ],
            [0.0, float(VMAX), 0.0, "0%"],
        )
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
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key_pressed)
        self.add_controller(keys)

    @property
    def value(self) -> int:
        return self._value

    def set_value(self, value: int) -> None:
        value = max(0, min(VMAX, int(value)))
        if value != self._value:
            self._value = value
            self.queue_draw()
            # An unavailable slider is announced as unavailable, regardless
            # of programmatic/internal values while its drawing is blank.
            if not self._dead:
                self.update_property(
                    [Gtk.AccessibleProperty.VALUE_NOW, Gtk.AccessibleProperty.VALUE_TEXT],
                    [float(value), f"{value}%"],
                )

    def set_hardware(self, touched: bool) -> None:
        if touched != self._hw:
            self._hw = touched
            self.queue_draw()

    def set_dead(self, dead: bool) -> None:
        if dead != self._dead:
            self._dead = dead
            self.set_sensitive(not dead)
            self.queue_draw()
            self.update_property(
                [Gtk.AccessibleProperty.VALUE_NOW, Gtk.AccessibleProperty.VALUE_TEXT],
                [0.0, "Unavailable"] if dead else [float(self._value), f"{self._value}%"],
            )

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
        if self._dead:
            return False
        previous = self._value
        self.set_value(self._value + (-2 if dy > 0 else 2))
        if self._value != previous:
            self.emit("moved", self._value)
        return True

    def _on_key_pressed(
        self,
        _controller: Gtk.EventControllerKey,
        keyval: int,
        _keycode: int,
        _state: Gdk.ModifierType,
    ) -> bool:
        """Make the focused vertical fader usable with standard slider keys."""
        if self._dead:
            return False
        if keyval in (Gdk.KEY_Up, Gdk.KEY_Right):
            value = self._value + 1
        elif keyval in (Gdk.KEY_Down, Gdk.KEY_Left):
            value = self._value - 1
        elif keyval == Gdk.KEY_Page_Up:
            value = self._value + 10
        elif keyval == Gdk.KEY_Page_Down:
            value = self._value - 10
        elif keyval == Gdk.KEY_Home:
            value = 0
        elif keyval == Gdk.KEY_End:
            value = VMAX
        else:
            return False
        previous = self._value
        self.set_value(value)
        if self._value != previous:
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

    #: Tick, gap, then the widest label at 8.5 px Andale Mono. "-18" and
    #: "-42" need every one of these pixels; 19 px cut them mid-glyph,
    #: which is what read as the ruler being clipped.
    WIDTH = 26

    def __init__(self, palette: dict[str, str], height: int) -> None:
        super().__init__()
        self._pal = palette
        # The tick labels are a purely visual ruler for the adjacent semantic
        # Meter widget; exposing this Cairo drawing area as another generic
        # object would add an unlabeled, non-interactive stop to the AT tree.
        self.set_accessible_role(Gtk.AccessibleRole.PRESENTATION)
        self.set_content_width(self.WIDTH)
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
            # Measure rather than assume: a label is drawn only where it
            # fits whole, and the first label sits far enough down that
            # its cap height stays inside the widget.
            extents = cr.text_extents(text)
            x = 6.0
            if x + extents.width > w:
                x = max(0.0, w - extents.width)
            cr.move_to(x, min(h - 1.0, max(7.0, y + 3.5)))
            cr.show_text(text)


class Sliver(Gtk.Button):
    """A minimized channel represented by a native, accessible GTK button."""

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
        # Keep the custom drawing inside a native button. GTK then supplies
        # the actual accessible button action and standard Space/Enter behavior
        # rather than asking assistive technology to operate a DrawingArea.
        canvas = Gtk.DrawingArea()
        canvas.set_content_width(length if horizontal else 24)
        canvas.set_content_height(24 if horizontal else length)
        canvas.set_draw_func(self._draw)
        canvas.set_can_target(False)
        canvas.set_accessible_role(Gtk.AccessibleRole.PRESENTATION)
        self._canvas = canvas
        self.set_child(canvas)
        self.update_property([Gtk.AccessibleProperty.LABEL], [f"Restore {name}"])

    def feed(self, level: float, muted: bool) -> None:
        self._level, self._muted = level, muted
        self._canvas.queue_draw()

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
