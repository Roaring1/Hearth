"""The LPD8 window: the controller itself, drawn, with every control live.

This started as a popover on one fader's ``K5`` chip, became a list of
dropdowns, and is now the shape of the thing on the desk: eight pad boxes
in two rows of four, eight knobs in two rows of four beside them, laid out
the way they are printed on an LPD8. A person looking for "the pad at the
bottom left" can point at the pad at the bottom left.

Clicking a pad or a knob selects it; one row underneath says what that
control does and is the only place anything is changed. That keeps eight
dropdowns off the picture -- the picture stays a picture -- while every
control stays reachable by mouse, by Tab, and by Enter.

The same rule as :mod:`hearth.ui.gtk4.setup` applies. Nothing here is
imported or built until somebody opens it, and the window reads the map
only while it is on screen: no poll, just a re-read on every open, which
also covers the user editing the script by hand.

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
#: jobs said shorter; the full wording is still what the detail row and the
#: tooltip show, so nothing is only ever seen abbreviated.
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


def _rgb(value: str) -> tuple[float, float, float]:
    """``#rrggbb`` as the 0-1 triple cairo wants."""
    text = value.lstrip("#")
    if len(text) != 6:
        return (0.5, 0.5, 0.5)
    return tuple(int(text[i : i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]


def _tag(text: str) -> Gtk.Label:
    label = Gtk.Label(label=text, xalign=0.0)
    label.add_css_class("setup-tag")
    return label


def _note(text: str) -> Gtk.Label:
    label = Gtk.Label(label=text, xalign=0.0)
    label.add_css_class("setup-note")
    label.set_wrap(True)
    return label


class _Pad(Gtk.Button):
    """One pad box, drawn where that pad sits on the hardware."""

    def __init__(self, pad: int, on_pick) -> None:
        super().__init__()
        self.pad = pad
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
        self.connect("clicked", lambda _b: on_pick("pad", pad))

    def show_job(self, text: str, tooltip: str) -> None:
        self.job.set_text(text)
        self.set_tooltip_text(tooltip)
        # An unused pad is drawn as an empty box rather than hidden: the
        # hardware still has eight pads whether or not the script uses them.
        if text:
            self.remove_css_class("free")
        else:
            self.add_css_class("free")


class _Knob(Gtk.Button):
    """One knob, drawn round, where that knob sits on the hardware."""

    def __init__(self, knob: int, pal: dict[str, str], on_pick) -> None:
        super().__init__()
        self.knob = knob
        self.pal = pal
        self.assigned = False
        self.selected = False
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
        self.connect("clicked", lambda _b: on_pick("knob", knob))

    def show_job(self, text: str, tooltip: str) -> None:
        self.job.set_text(text)
        self.set_tooltip_text(tooltip)
        self.assigned = bool(text)
        self.face.queue_draw()

    def set_selected(self, selected: bool) -> None:
        self.selected = selected
        self.face.queue_draw()

    def _draw(self, _area: Gtk.DrawingArea, cr, width: int, height: int) -> None:
        pal = self.pal
        radius = min(width, height) / 2 - 3
        cx, cy = width / 2, height / 2
        cr.arc(cx, cy, radius, 0, 2 * math.pi)
        cr.set_source_rgb(*_rgb(pal["cap"] if self.assigned else pal["view-alt"]))
        cr.fill()
        cr.arc(cx, cy, radius, 0, 2 * math.pi)
        cr.set_source_rgb(*_rgb(pal["accent"] if self.selected else pal["view"]))
        cr.set_line_width(2.0 if self.selected else 1.0)
        cr.stroke()
        # The notch is drawn at twelve o'clock on every knob and never
        # moves: this window maps controls, it does not read their
        # positions, and a notch that wandered would claim otherwise.
        cr.move_to(cx, cy - radius * 0.2)
        cr.line_to(cx, cy - radius * 0.8)
        cr.set_source_rgb(*_rgb(pal["fg"] if self.assigned else pal["fg-dim"]))
        cr.set_line_width(2.0)
        cr.set_line_cap(1)  # CAIRO_LINE_CAP_ROUND
        cr.stroke()


class RemapWindow(Gtk.Window):
    """The LPD8, drawn to scale of itself, with every control editable."""

    def __init__(self, parent: Gtk.Window | None = None) -> None:
        super().__init__(title="LPD8 Controls", transient_for=parent)
        self.add_css_class("hearth")
        self.set_default_size(560, -1)
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
        self._selected: tuple[str, int] | None = None

        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        column.set_margin_top(12)
        column.set_margin_bottom(14)
        column.set_margin_start(14)
        column.set_margin_end(14)
        self.set_child(column)

        column.append(self._build_device())
        column.append(self._build_detail())
        column.append(self._build_state())

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
                widget = _Pad(pad, self._on_pick)
                self._pads[pad] = widget
                pads.attach(widget, col, row, 1, 1)
        panel.append(pads)

        knobs = Gtk.Grid(row_spacing=4, column_spacing=4)
        knobs.set_valign(Gtk.Align.CENTER)
        knobs.set_column_homogeneous(True)
        for row, line in enumerate(KNOB_ROWS):
            for col, knob in enumerate(line):
                widget = _Knob(knob, self.pal, self._on_pick)
                self._knobs[knob] = widget
                knobs.attach(widget, col, row, 1, 1)
        panel.append(knobs)
        return panel

    def _build_detail(self) -> Gtk.Widget:
        """The one place anything is changed: the selected control's job."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.add_css_class("lpd8-detail")

        self.hint = _note("Pick a pad or a knob above to change what it does.")
        box.append(self.hint)

        self.detail_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.detail_name = Gtk.Label(label="", xalign=0.0)
        self.detail_name.add_css_class("lpd8-sel")
        self.detail_name.set_hexpand(True)
        self.detail_row.append(self.detail_name)
        self.choice = Gtk.DropDown.new_from_strings([""])
        self.choice_handler = self.choice.connect("notify::selected", self._on_choice)
        self.detail_row.append(self.choice)
        self.detail_row.set_visible(False)
        box.append(self.detail_row)

        self.clash = Gtk.Label(label="", xalign=0.0)
        self.clash.add_css_class("setup-clash")
        self.clash.set_wrap(True)
        self.clash.set_visible(False)
        box.append(self.clash)
        return box

    def _build_state(self) -> Gtk.Widget:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.state = Gtk.Label(label="\u2026", xalign=0.0)
        self.state.set_hexpand(True)
        self.state.set_wrap(True)
        self.state.add_css_class("setup-note")
        row.append(self.state)
        restart = Gtk.Button(label="Restart")
        restart.add_css_class("setup-act")
        restart.set_tooltip_text(
            "Restart lpd8-mixer, the service that turns knob and pad presses "
            "into volume and mute changes."
        )
        restart.connect("clicked", self._on_restart_clicked)
        row.append(restart)
        return row

    # -- reading ---------------------------------------------------------
    def refresh(self) -> None:
        """Re-read the script and the unit, and show what they say."""
        self._refresh_map()
        self._refresh_state()

    def _refresh_state(self) -> None:
        try:
            # is_active() folds "systemd did not answer" into False, which
            # would print a confident lie. The raw state keeps three
            # answers: running, not running, and don't know.
            state = services_mod.states([lpd8map.UNIT]).get(lpd8map.UNIT)
        except Exception:  # pragma: no cover - a dead systemd is not a crash
            state = None
        if state in (None, "", services_mod.UNKNOWN):
            self.state.set_text(
                "Cannot tell whether the LPD8 service is running \u2014 systemd did not answer"
            )
        elif state == "active":
            self.state.set_text("LPD8 service running \u2014 changes apply straight away")
        else:
            self.state.set_text(
                "LPD8 service is not running \u2014 knobs and pads do nothing until it is restarted"
            )

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
            self._select(None)
            self.hint.set_text(
                f"No map found in {lpd8map.SCRIPT}. Nothing here can be changed "
                "until that script is back."
            )
            self.hint.set_visible(True)
            self.clash.set_visible(False)
            return

        if self._selected is not None:
            self._fill_choice()
        self._show_clashes(knobs, pads)

    def _show_clashes(self, knobs: dict[str, int], pads: dict[int, int]) -> None:
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
        self.clash.set_text("; ".join(lines))
        self.clash.set_visible(bool(lines))

    # -- selection -------------------------------------------------------
    def _on_pick(self, kind: str, number: int) -> None:
        self._select((kind, number))

    def _select(self, choice: tuple[str, int] | None) -> None:
        self._selected = choice
        for pad, widget in self._pads.items():
            if choice == ("pad", pad):
                widget.add_css_class("sel")
            else:
                widget.remove_css_class("sel")
        for knob, widget in self._knobs.items():
            widget.set_selected(choice == ("knob", knob))
        if choice is None:
            self.detail_row.set_visible(False)
            self.hint.set_visible(True)
            return
        self.hint.set_visible(False)
        self.detail_row.set_visible(True)
        self._fill_choice()

    def _fill_choice(self) -> None:
        """Put the selected control's job list in the detail dropdown."""
        if self._selected is None:
            return
        kind, number = self._selected
        if kind == "pad":
            self.detail_name.set_text(f"Pad {number} does")
            names = [s.label for s in lpd8map.PAD_SLOTS]
            slots = sorted(self._pad_use.get(number, []))
            order = [s.slot for s in lpd8map.PAD_SLOTS]
            index = order.index(slots[0]) if slots else None
        else:
            self.detail_name.set_text(f"Knob {number} moves")
            names = [a.label for a in lpd8map.ASSIGNMENTS]
            consts = self._knob_use.get(number, [])
            order = [a.const for a in lpd8map.ASSIGNMENTS]
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
        if self._selected is None:
            return
        kind, number = self._selected
        index = int(drop.get_selected())
        if kind == "pad":
            if index >= len(lpd8map.PAD_SLOTS):
                return
            slot = lpd8map.PAD_SLOTS[index].slot
            self._write(lambda: lpd8map.set_pad(slot, number))
        else:
            if index >= len(lpd8map.ASSIGNMENTS):
                return
            const = lpd8map.ASSIGNMENTS[index].const
            self._write(lambda: lpd8map.set_knob(const, number))

    def _write(self, edit) -> None:
        """Run one edit, report it in words, and queue the restart."""
        try:
            changed = edit()
        except (OSError, ValueError) as exc:
            log.warning("LPD8 remap failed: %s", exc)
            self.state.set_text(f"Could not save that: {exc}")
            return
        if not changed:
            return
        self._refresh_map()
        self.state.set_text("Saved \u2014 restarting the LPD8 service\u2026")
        self._queue_restart()

    def _queue_restart(self) -> None:
        if self._restart_id:
            GLib.source_remove(self._restart_id)
        self._restart_id = GLib.timeout_add(RESTART_DELAY_MS, self._do_restart)

    def _do_restart(self) -> bool:
        self._restart_id = 0
        self._restart()
        return False

    def _on_restart_clicked(self, _button: Gtk.Button) -> None:
        self._restart()

    def _restart(self) -> None:
        try:
            services_mod.restart(lpd8map.UNIT)
        except Exception as exc:  # pragma: no cover - surfaced, not raised
            log.warning("could not restart %s: %s", lpd8map.UNIT, exc)
            self.state.set_text(f"Could not restart the LPD8 service: {exc}")
            return
        self._refresh_state()

    def _on_close(self, *_args) -> bool:
        # A queued restart must not outlive the window that promised it.
        if self._restart_id:
            GLib.source_remove(self._restart_id)
            self._restart_id = 0
            self._restart()
        return False
