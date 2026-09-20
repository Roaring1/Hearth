"""The LPD8 window: the controller itself, drawn, with every control live.

This started as a popover on one fader's ``K5`` chip, became a list of
dropdowns, and is now the shape of the thing on the desk: eight pad boxes
in two rows of four, eight knobs in two rows of four beside them, laid out
the way they are printed on an LPD8. A person looking for "the pad at the
bottom left" can point at the pad at the bottom left.

The window is the picture and nothing else -- no hint line, no status
footer, no button bar. Changing a control happens on the control: click it
or right-click it and a dropdown opens over it listing what it can do.

The same rule as :mod:`hearth.ui.gtk4.setup` applies. Nothing here is
imported or built until somebody opens it, and the window reads the map
only while it is on screen: no poll, just a re-read on every open, which
also covers the user editing the script by hand.

The controls also show where they actually are. A knob's notch sits at the
volume that knob last wrote, and a pad glows while the bus it watches is
audible, matching the pad's own LED. None of that is polled from here: the
mixer hands this window the snapshot it was already taking, so an open
LPD8 window costs a few redraws and no extra reads at all.

Every edit is written straight to ``~/bin/lpd8_mixer.sh`` and the unit is
restarted, because a mapping the hardware is not using yet is not a
mapping. The restart is deferred a moment so changing three controls in a
row costs one restart.
"""

from __future__ import annotations

import logging
import math

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import GLib, Gtk  # noqa: E402 - must follow require_version

from hearth import leds as leds_mod  # noqa: E402
from hearth import lpd8map  # noqa: E402
from hearth import services as services_mod  # noqa: E402
from hearth.ui.gtk4 import style as style_mod  # noqa: E402

log = logging.getLogger(__name__)

#: How long to wait after an edit before restarting the unit. Long enough
#: that moving three controls in a row is one restart, short enough that
#: the hardware follows the window while the user is still looking at it.
RESTART_DELAY_MS = 900

#: The pads as they are printed: 5-8 across the top, 1-4 across the bottom.
PAD_ROWS: tuple[tuple[int, ...], ...] = ((5, 6, 7, 8), (1, 2, 3, 4))

#: The knobs as they are printed: K1-K4 on top, K5-K8 below.
KNOB_ROWS: tuple[tuple[int, ...], ...] = ((1, 2, 3, 4), (5, 6, 7, 8))

#: A pad box is about two words wide, so the full sentence from
#: :data:`hearth.lpd8map.PAD_SLOTS` will not fit on it. These are the same
#: jobs said shorter; the full wording is on the tooltip and in the
#: dropdown, so nothing is only ever seen abbreviated.
PAD_SHORT: dict[int, str] = {
    1: "Music mute",
    2: "Chat mute",
    3: "Headset source",
    4: "Headset mute",
    5: "Save report",
    6: "Mic mute",
    7: "Game mute",
    8: "Desk speakers",
}

#: The same for knobs, under a 56-pixel cap.
KNOB_SHORT: dict[str, str] = {
    "CC_VM_GAME": "Game",
    "CC_VM_GAME_ALT": "Game 2",
    "CC_VM_CHAT": "Chat",
    "CC_VM_MUSIC": "Music",
    "CC_MIC_VOL": "Mic B1",
}


#: Which pad watches which bus, inverted from :mod:`hearth.leds` so the
#: two cannot drift apart. Pads 3, 5 and 8 fire an action rather than hold
#: a state, so they have no light to mirror and never glow.
PAD_SINK: dict[int, str] = {pad: sink for sink, pad in leds_mod.PAD_FOR_SINK.items()}

#: How far a knob turns from centre, in degrees, at each end of its travel.
KNOB_SWEEP = 135.0


def _rgb(value: str) -> tuple[float, float, float]:
    """``#rrggbb`` as the 0-1 triple cairo wants."""
    text = value.lstrip("#")
    if len(text) != 6:
        return (0.5, 0.5, 0.5)
    return tuple(int(text[i : i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]


class _Control:
    """What a pad and a knob share: they open their own menu.

    Both the plain click and the right-click open the same dropdown, so
    the discoverable gesture and the one you asked for do the same thing,
    and the keyboard reaches it through the button's own activation.
    """

    kind = ""
    number = 0

    def _wire(self, on_menu) -> None:
        self.connect("clicked", lambda _b: on_menu(self))  # type: ignore[attr-defined]
        secondary = Gtk.GestureClick()
        secondary.set_button(3)
        secondary.connect("pressed", lambda *_a: on_menu(self))
        self.add_controller(secondary)  # type: ignore[attr-defined]


class _Pad(Gtk.Button, _Control):
    """One pad box, drawn where that pad sits on the hardware."""

    def __init__(self, pad: int, on_menu) -> None:
        super().__init__()
        self.kind = "pad"
        self.number = pad
        self.add_css_class("lpd8-pad")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.cap = Gtk.Label(label=f"PAD {pad}", xalign=0.0)
        self.cap.add_css_class("lpd8-cap")
        self.job = Gtk.Label(label="", xalign=0.0)
        self.job.add_css_class("lpd8-job")
        self.job.set_wrap(True)
        self.job.set_lines(2)
        self.job.set_ellipsize(3)  # PANGO_ELLIPSIZE_END
        self.job.set_valign(Gtk.Align.START)
        self.job.set_vexpand(True)
        box.append(self.cap)
        box.append(self.job)
        self.set_child(box)
        self._wire(on_menu)

    def show_job(self, text: str, tooltip: str) -> None:
        self.job.set_text(text)
        self.set_tooltip_text(tooltip)
        # An unused pad is drawn as an empty box rather than hidden: the
        # hardware still has eight pads whether or not the script uses them.
        if text:
            self.remove_css_class("free")
        else:
            self.add_css_class("free")

    def set_live(self, lit: bool | None) -> None:
        """Glow while the bus this pad watches is audible.

        Same rule as the hardware LED: lit means you can hear it. ``None``
        is a pad that holds no state, and it stays plain rather than
        claiming its light is off.
        """
        if lit:
            self.add_css_class("live")
        else:
            self.remove_css_class("live")

    def set_open(self, open_: bool) -> None:
        if open_:
            self.add_css_class("sel")
        else:
            self.remove_css_class("sel")


class _Knob(Gtk.Button, _Control):
    """One knob, drawn round, where that knob sits on the hardware."""

    def __init__(self, knob: int, pal: dict[str, str], on_menu) -> None:
        super().__init__()
        self.kind = "knob"
        self.number = knob
        self.pal = pal
        self.assigned = False
        self.open = False
        #: 0-1, or None when nothing has said where this knob is.
        self.level: float | None = None
        self.add_css_class("lpd8-knob")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        box.set_halign(Gtk.Align.CENTER)
        self.face = Gtk.DrawingArea()
        self.face.set_content_width(44)
        self.face.set_content_height(44)
        self.face.set_draw_func(self._draw)
        box.append(self.face)
        self.cap = Gtk.Label(label=f"K{knob}")
        self.cap.add_css_class("lpd8-cap")
        self.cap.set_halign(Gtk.Align.CENTER)
        self.job = Gtk.Label(label="")
        self.job.add_css_class("lpd8-job")
        self.job.set_halign(Gtk.Align.CENTER)
        # Without a cap the longest bus name would widen its own column and
        # the eight knobs would stop being a grid.
        self.job.set_ellipsize(3)
        self.job.set_max_width_chars(7)
        box.append(self.cap)
        box.append(self.job)
        self.set_child(box)
        self._wire(on_menu)

    def show_job(self, text: str, tooltip: str) -> None:
        self.job.set_text(text)
        self.set_tooltip_text(tooltip)
        self.assigned = bool(text)
        self.face.queue_draw()

    def set_level(self, level: float | None) -> None:
        """Point the notch at *level*, or straight up if it is unknown.

        Redraws only on a real change: the mixer pushes state twice a
        second and a volume that has not moved must not cost a frame.
        """
        if level is not None:
            level = max(0.0, min(1.0, level))
            if self.level is not None and abs(level - self.level) < 0.005:
                return
        elif self.level is None:
            return
        self.level = level
        self.face.queue_draw()

    def set_open(self, open_: bool) -> None:
        self.open = open_
        self.face.queue_draw()

    def _draw(self, _area: Gtk.DrawingArea, cr, width: int, height: int) -> None:
        pal = self.pal
        radius = min(width, height) / 2 - 3
        cx, cy = width / 2, height / 2
        cr.arc(cx, cy, radius, 0, 2 * math.pi)
        cr.set_source_rgb(*_rgb(pal["cap"] if self.assigned else pal["view-alt"]))
        cr.fill()
        cr.arc(cx, cy, radius, 0, 2 * math.pi)
        cr.set_source_rgb(*_rgb(pal["accent"] if self.open else pal["view"]))
        cr.set_line_width(2.0 if self.open else 1.0)
        cr.stroke()
        # The knob cannot be asked where it is, but the bus it drives can:
        # the volume it last wrote *is* its position. With nothing to go on
        # the notch points straight up and no arc is drawn, rather than
        # inventing a position.
        sweep = math.radians(KNOB_SWEEP)
        angle = 0.0 if self.level is None else (self.level - 0.5) * 2 * sweep
        if self.level is not None:
            cr.arc(cx, cy, radius * 0.72, -math.pi / 2 - sweep, -math.pi / 2 + angle)
            cr.set_source_rgb(*_rgb(pal["accent"]))
            cr.set_line_width(2.0)
            cr.stroke()
        dx, dy = math.sin(angle), -math.cos(angle)
        cr.move_to(cx + dx * radius * 0.2, cy + dy * radius * 0.2)
        cr.line_to(cx + dx * radius * 0.8, cy + dy * radius * 0.8)
        cr.set_source_rgb(*_rgb(pal["fg"] if self.assigned else pal["fg-dim"]))
        cr.set_line_width(2.0)
        cr.set_line_cap(1)  # CAIRO_LINE_CAP_ROUND
        cr.stroke()


class RemapWindow(Gtk.Window):
    """The LPD8, drawn, with every control editable on the control."""

    def __init__(self, parent: Gtk.Window | None = None) -> None:
        super().__init__(title="LPD8 Controls", transient_for=parent)
        self.add_css_class("hearth")
        self.set_resizable(False)
        # Same bargain as Setup: built once, hidden rather than destroyed,
        # and it reads nothing while hidden.
        self.set_hide_on_close(True)

        self.pal = style_mod.palette()
        self._restart_id = 0
        self._pads: dict[int, _Pad] = {}
        self._knobs: dict[int, _Knob] = {}
        #: pad -> slots it triggers, and knob -> constants it drives.
        self._pad_use: dict[int, list[int]] = {}
        self._knob_use: dict[int, list[str]] = {}
        self._open: _Pad | _Knob | None = None
        #: sink name -> live state, pushed in by the mixer. Never read here.
        self._live: dict[str, object] = {}

        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        column.set_margin_top(12)
        column.set_margin_bottom(12)
        column.set_margin_start(14)
        column.set_margin_end(14)
        self.set_child(column)

        column.append(self._build_device())

        # The only line of text in the window, and it is not a footer: it
        # is hidden unless something is actually wrong.
        self.problem = Gtk.Label(label="", xalign=0.0)
        self.problem.add_css_class("setup-clash")
        self.problem.set_wrap(True)
        self.problem.set_visible(False)
        column.append(self.problem)

        self._build_menu()

        escape = Gtk.EventControllerKey()
        escape.connect("key-pressed", self._on_key)
        self.add_controller(escape)

        self.connect("map", lambda *_a: self.refresh())
        self.connect("close-request", self._on_close)

    # -- construction ----------------------------------------------------
    def _build_device(self) -> Gtk.Widget:
        """The controller: pads on the left, knobs on the right, as printed."""
        panel = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        panel.add_css_class("lpd8")

        pads = Gtk.Grid(row_spacing=6, column_spacing=6)
        for row, line in enumerate(PAD_ROWS):
            for col, pad in enumerate(line):
                widget = _Pad(pad, self._open_menu)
                self._pads[pad] = widget
                pads.attach(widget, col, row, 1, 1)
        panel.append(pads)

        knobs = Gtk.Grid(row_spacing=4, column_spacing=4)
        knobs.set_valign(Gtk.Align.CENTER)
        knobs.set_column_homogeneous(True)
        for row, line in enumerate(KNOB_ROWS):
            for col, knob in enumerate(line):
                widget = _Knob(knob, self.pal, self._open_menu)
                self._knobs[knob] = widget
                knobs.attach(widget, col, row, 1, 1)
        panel.append(knobs)

        # Right-clicking the gap between two pads is still pointing at a
        # pad, so the slab takes the click the controls did not and hands
        # it to whichever control is nearest the pointer.
        slab = Gtk.GestureClick()
        slab.set_button(3)
        slab.connect("pressed", self._on_slab_press, panel)
        panel.add_controller(slab)
        self._panel = panel
        return panel

    def _on_slab_press(self, _g, _n: int, x: float, y: float, panel: Gtk.Widget) -> None:
        widget = self._nearest(x, y, panel)
        if widget is not None:
            self._open_menu(widget)

    def _nearest(self, x: float, y: float, panel: Gtk.Widget) -> _Pad | _Knob | None:
        """The control whose box is closest to that point on the slab."""
        best: _Pad | _Knob | None = None
        best_d = float("inf")
        for widget in (*self._pads.values(), *self._knobs.values()):
            ok, rect = widget.compute_bounds(panel)
            if not ok:
                continue
            left, top = rect.origin.x, rect.origin.y
            right, bottom = left + rect.size.width, top + rect.size.height
            dx = max(left - x, 0.0, x - right)
            dy = max(top - y, 0.0, y - bottom)
            distance = dx * dx + dy * dy
            if distance < best_d:
                best_d, best = distance, widget
        return best

    def _build_menu(self) -> None:
        """One dropdown, re-parented onto whichever control was clicked.

        One popover rather than sixteen: the list is rebuilt per control
        anyway, and sixteen live dropdowns is fifteen widgets built for a
        control nobody touched.
        """
        self.menu = Gtk.Popover()
        self.menu.set_autohide(True)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.add_css_class("lpd8-detail")
        self.menu_name = Gtk.Label(label="", xalign=0.0)
        self.menu_name.add_css_class("lpd8-sel")
        box.append(self.menu_name)
        self.choice = Gtk.DropDown.new_from_strings([""])
        self.choice_handler = self.choice.connect("notify::selected", self._on_choice)
        box.append(self.choice)
        self.menu.set_child(box)
        self.menu.connect("closed", self._on_menu_closed)

    # -- reading ---------------------------------------------------------
    def refresh(self) -> None:
        """Re-read the script and show what it says."""
        self._refresh_map()

    def _service_note(self) -> str:
        """Say the service is down only when it is known to be down.

        ``is_active()`` folds "systemd did not answer" into False, which
        would print a confident lie. The raw state keeps three answers:
        running, not running, and don't know -- and only the middle one is
        worth a line on screen.
        """
        try:
            state = services_mod.states([lpd8map.UNIT]).get(lpd8map.UNIT)
        except Exception:  # pragma: no cover - a dead systemd is not a crash
            return ""
        if state in (None, "", services_mod.UNKNOWN, "active"):
            return ""
        return "The LPD8 service is not running \u2014 knobs and pads do nothing until it is"

    def _refresh_map(self) -> None:
        try:
            knobs = lpd8map.read_map()
            pads = lpd8map.read_pad_map()
        except Exception:  # pragma: no cover - a missing script is not a crash
            knobs, pads = {}, {}

        # The script is written the other way round -- job to control --
        # and the picture needs control to job, so it is inverted here.
        self._pad_use = {}
        for slot, pad in pads.items():
            self._pad_use.setdefault(pad, []).append(slot)
        self._knob_use = {}
        for const, cc in knobs.items():
            knob = lpd8map.cc_to_knob(cc)
            if knob is not None:
                self._knob_use.setdefault(knob, []).append(const)

        slot_labels = {s.slot: s.label for s in lpd8map.PAD_SLOTS}
        for pad, widget in self._pads.items():
            slots = sorted(self._pad_use.get(pad, []))
            short = " + ".join(PAD_SHORT.get(s, slot_labels.get(s, str(s))) for s in slots)
            full = " and ".join(slot_labels.get(s, str(s)) for s in slots)
            widget.show_job(short, full or f"Pad {pad} does nothing yet")

        knob_labels = {a.const: a.label for a in lpd8map.ASSIGNMENTS}
        for knob, widget in self._knobs.items():
            consts = self._knob_use.get(knob, [])
            short = " + ".join(KNOB_SHORT.get(c, knob_labels.get(c, c)) for c in consts)
            full = " and ".join(knob_labels.get(c, c) for c in consts)
            widget.show_job(short, full or f"Knob {knob} moves nothing yet")

        usable = bool(knobs or pads)
        for widget in (*self._pads.values(), *self._knobs.values()):
            widget.set_sensitive(usable)
        if not usable:
            self._say(
                f"No map found in {lpd8map.SCRIPT} \u2014 nothing here can be "
                "changed until that script is back"
            )
            return

        if self._open is not None:
            self._fill_choice(self._open)
        self._say("; ".join(x for x in (self._clashes(knobs, pads), self._service_note()) if x))
        # A control that just changed job now watches a different bus.
        self._paint_live()

    def apply_live(self, states: dict[str, object]) -> None:
        """Take the mixer's snapshot: *states* is sink name -> channel state.

        Anything with ``volume``, ``muted`` and ``present`` attributes
        will do, which keeps this window free of the mixer's own types.
        Ignored while the window is not on screen, so a closed LPD8
        window costs nothing per snapshot.
        """
        if not self.get_mapped():
            return
        self._live = states
        self._paint_live()

    def _paint_live(self) -> None:
        """Put every control where the rig says it is."""
        if not self._live:
            return
        sink_for = {a.const: a.sink for a in lpd8map.ASSIGNMENTS}
        for knob, widget in self._knobs.items():
            level: float | None = None
            for const in self._knob_use.get(knob, []):
                state = self._live.get(sink_for.get(const, ""))
                if state is not None and getattr(state, "present", True):
                    volume = int(getattr(state, "volume", 0))
                    level = max(0, min(100, volume)) / 100
                    break
            widget.set_level(level)
        for pad, widget in self._pads.items():
            state = self._live.get(PAD_SINK.get(pad, ""))
            lit: bool | None = None
            if state is not None and getattr(state, "present", True):
                lit = not bool(getattr(state, "muted", False))
            widget.set_live(lit)

    def _clashes(self, knobs: dict[str, int], pads: dict[int, int]) -> str:
        labels = {a.const: a.label for a in lpd8map.ASSIGNMENTS}
        slot_labels = {s.slot: s.label for s in lpd8map.PAD_SLOTS}
        lines: list[str] = []
        for cc, consts in sorted(lpd8map.conflicts(knobs).items()):
            sinks = {a.sink for a in lpd8map.ASSIGNMENTS if a.const in consts}
            if len(sinks) == 1:
                # Two knobs on one bus is the rig's own choice; one knob on
                # one bus twice is not worth shouting about either.
                continue
            knob = lpd8map.cc_to_knob(cc)
            name = f"Knob {knob}" if knob else f"CC {cc}"
            lines.append(f"{name} moves " + " and ".join(labels.get(c, c) for c in consts))
        for pad, slots in sorted(lpd8map.pad_conflicts(pads).items()):
            joined = " and ".join(slot_labels.get(s, str(s)).lower() for s in slots)
            lines.append(f"Pad {pad} is set to {joined} \u2014 only the first happens")
        return "; ".join(lines)

    def _say(self, text: str) -> None:
        """Show a problem, or nothing at all."""
        self.problem.set_text(text)
        self.problem.set_visible(bool(text))

    # -- the menu --------------------------------------------------------
    def _open_menu(self, widget: _Pad | _Knob) -> None:
        if not widget.get_sensitive():
            return
        if self._open is not None:
            self._open.set_open(False)
        self._open = widget
        widget.set_open(True)
        self._fill_choice(widget)
        if self.menu.get_parent() is not widget:
            self.menu.unparent()
            self.menu.set_parent(widget)
        self.menu.popup()

    def _on_menu_closed(self, _popover: Gtk.Popover) -> None:
        if self._open is not None:
            self._open.set_open(False)
            self._open = None

    def _fill_choice(self, widget: _Pad | _Knob) -> None:
        """Put that control's job list in the dropdown."""
        number = widget.number
        if widget.kind == "pad":
            self.menu_name.set_text(f"Pad {number} does")
            names = [s.label for s in lpd8map.PAD_SLOTS]
            slots = sorted(self._pad_use.get(number, []))
            order = [s.slot for s in lpd8map.PAD_SLOTS]
            index = order.index(slots[0]) if slots else None
        else:
            self.menu_name.set_text(f"Knob {number} moves")
            names = [a.label for a in lpd8map.ASSIGNMENTS]
            consts = self._knob_use.get(number, [])
            order = [a.const for a in lpd8map.ASSIGNMENTS]  # type: ignore[misc]
            index = order.index(consts[0]) if consts else None
        # A control the script does not use gets a trailing entry saying so,
        # rather than a silently wrong first row. Picking anything else
        # replaces it; it is never invented for a control that is mapped.
        if index is None:
            names = [*names, "nothing yet"]
            index = len(names) - 1
        self.choice.handler_block(self.choice_handler)
        self.choice.set_model(Gtk.StringList.new(names))
        self.choice.set_selected(index)
        self.choice.handler_unblock(self.choice_handler)

    def _on_key(self, _c, keyval: int, _code: int, _state) -> bool:
        if keyval == 0xFF1B:  # Escape
            self.close()
            return True
        return False

    # -- writing ---------------------------------------------------------
    def _on_choice(self, drop: Gtk.DropDown, _param) -> None:
        if self._open is None:
            return
        widget = self._open
        number = widget.number
        index = int(drop.get_selected())
        if widget.kind == "pad":
            if index >= len(lpd8map.PAD_SLOTS):
                return
            slot = lpd8map.PAD_SLOTS[index].slot
            self._write(lambda: lpd8map.set_pad(slot, number))
        else:
            if index >= len(lpd8map.ASSIGNMENTS):
                return
            const = lpd8map.ASSIGNMENTS[index].const
            self._write(lambda: lpd8map.set_knob(const, number))
        self.menu.popdown()

    def _write(self, edit) -> None:
        """Run one edit and queue the restart."""
        try:
            changed = edit()
        except (OSError, ValueError) as exc:
            log.warning("LPD8 remap failed: %s", exc)
            self._say(f"Could not save that: {exc}")
            return
        if not changed:
            return
        self._refresh_map()
        self._queue_restart()

    def _queue_restart(self) -> None:
        if self._restart_id:
            GLib.source_remove(self._restart_id)
        self._restart_id = GLib.timeout_add(RESTART_DELAY_MS, self._do_restart)

    def _do_restart(self) -> bool:
        self._restart_id = 0
        self._restart()
        return False

    def _restart(self) -> None:
        try:
            services_mod.restart(lpd8map.UNIT)
        except Exception as exc:  # pragma: no cover - surfaced, not raised
            log.warning("could not restart %s: %s", lpd8map.UNIT, exc)
            self._say(f"Could not restart the LPD8 service: {exc}")

    def _on_close(self, *_args) -> bool:
        # A queued restart must not outlive the window that promised it.
        if self._restart_id:
            GLib.source_remove(self._restart_id)
            self._restart_id = 0
            self._restart()
        return False
