"""The LPD8 window: which knob moves which bus, which pad does which job.

This was a popover hanging off one fader's little ``K5`` chip, which meant
the pads -- half the controller -- had nowhere to live at all, and the knob
map could only be seen one bus at a time. It is a window now: every knob and
every pad on one page, so a person can see the whole surface and change any
part of it.

The same rule as :mod:`hearth.ui.gtk4.setup` applies. Nothing here is
imported or built until somebody opens it, and the window reads the map only
while it is on screen. There is no poll at all: the script only changes when
this window changes it, or when the user edits it by hand, and re-reading on
every open covers the second case.

Every edit is written straight to ``~/bin/lpd8_mixer.sh`` and the unit is
restarted, because a mapping the hardware is not using yet is not a mapping.
The restart is deferred a moment so changing three rows costs one restart.
"""

from __future__ import annotations

import logging

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import GLib, Gtk  # noqa: E402 - must follow require_version

from hearth import lpd8map  # noqa: E402
from hearth import services as services_mod  # noqa: E402

log = logging.getLogger(__name__)

#: How long to wait after an edit before restarting the unit. Long enough
#: that moving three rows in a row is one restart, short enough that the
#: hardware follows the window while the user is still looking at it.
RESTART_DELAY_MS = 900


def _tag(text: str) -> Gtk.Label:
    label = Gtk.Label(label=text, xalign=0.0)
    label.add_css_class("setup-tag")
    return label


def _note(text: str) -> Gtk.Label:
    label = Gtk.Label(label=text, xalign=0.0)
    label.add_css_class("setup-note")
    label.set_wrap(True)
    return label


class _Row:
    """One line: what it controls, and the hardware it is wired to.

    Holds its own handler id so the window can set the dropdown from the
    file without the dropdown writing it straight back.
    """

    def __init__(self, label: str, choices: list[str], on_change) -> None:
        self.box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.name = Gtk.Label(label=label, xalign=0.0)
        self.name.set_hexpand(True)
        self.name.set_ellipsize(3)  # PANGO_ELLIPSIZE_END
        self.box.append(self.name)

        self.choices = list(choices)
        self.drop = Gtk.DropDown.new_from_strings(self.choices)
        self.drop.set_tooltip_text(f"Hardware control for {label.lower()}")
        self.handler = self.drop.connect("notify::selected", on_change)
        self.box.append(self.drop)

    def show_choice(self, index: int, extra: str | None = None) -> None:
        """Select *index*, adding *extra* as a trailing entry when given.

        ``extra`` is how an unrecognised value is shown -- a CC no knob
        sends, a slot with no pad. It is selectable-looking but always
        replaced the moment a real choice is made, and it is never invented
        when the file is normal.
        """
        wanted = self.choices + ([extra] if extra else [])
        model = self.drop.get_model()
        current = [model.get_string(i) for i in range(model.get_n_items())]
        self.drop.handler_block(self.handler)
        if current != wanted:
            self.drop.set_model(Gtk.StringList.new(wanted))
        self.drop.set_selected(index)
        self.drop.handler_unblock(self.handler)

    @property
    def selected(self) -> int:
        return int(self.drop.get_selected())


class RemapWindow(Gtk.Window):
    """Every LPD8 control on one page, each one editable."""

    def __init__(self, parent: Gtk.Window | None = None) -> None:
        super().__init__(title="LPD8 Controls", transient_for=parent)
        self.add_css_class("hearth")
        self.set_default_size(420, -1)
        # Same bargain as Setup: built once, hidden rather than destroyed,
        # and it reads nothing while hidden.
        self.set_hide_on_close(True)

        self._restart_id = 0
        self._knob_rows: dict[str, _Row] = {}
        self._pad_rows: dict[int, _Row] = {}

        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        column.set_margin_top(12)
        column.set_margin_bottom(14)
        column.set_margin_start(14)
        column.set_margin_end(14)

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_propagate_natural_height(True)
        scroller.set_max_content_height(760)
        scroller.set_child(column)
        self.set_child(scroller)

        column.append(self._build_state())
        column.append(self._build_knobs())
        column.append(self._build_pads())

        self.connect("map", lambda *_a: self.refresh())
        self.connect("close-request", self._on_close)

    # -- construction ----------------------------------------------------
    def _build_state(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.state = Gtk.Label(label="\u2026", xalign=0.0)
        self.state.set_hexpand(True)
        self.state.set_wrap(True)
        row.append(self.state)
        restart = Gtk.Button(label="Restart")
        restart.add_css_class("setup-act")
        restart.set_tooltip_text(
            "Restart lpd8-mixer, the service that turns knob and pad presses "
            "into volume and mute changes."
        )
        restart.connect("clicked", self._on_restart_clicked)
        row.append(restart)
        box.append(row)
        self.clash = Gtk.Label(label="", xalign=0.0)
        self.clash.add_css_class("setup-clash")
        self.clash.set_wrap(True)
        self.clash.set_visible(False)
        box.append(self.clash)
        return box

    def _build_knobs(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.append(_tag("KNOBS"))
        choices = [f"Knob {knob}" for knob in lpd8map.KNOBS]
        for assignment in lpd8map.ASSIGNMENTS:
            row = _Row(assignment.label, choices, self._on_knob_changed)
            row.drop.set_name(f"knob:{assignment.const}")
            self._knob_rows[assignment.const] = row
            box.append(row.box)
        self.knob_note = _note("")
        box.append(self.knob_note)
        return box

    def _build_pads(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.append(_tag("PADS"))
        choices = [f"Pad {pad}" for pad in lpd8map.PADS]
        for pad_slot in lpd8map.PAD_SLOTS:
            row = _Row(pad_slot.label, choices, self._on_pad_changed)
            row.drop.set_name(f"pad:{pad_slot.slot}")
            self._pad_rows[pad_slot.slot] = row
            box.append(row.box)
        box.append(
            _note(
                "A lit pad means that sound is on. Lights only follow the "
                "mixer when the LPD8 is in NOTE TOGGLE mode."
            )
        )
        return box

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

        if not knobs and not pads:
            self.knob_note.set_visible(True)
            self.knob_note.set_text(
                f"No map found in {lpd8map.SCRIPT}. Nothing here can be changed "
                "until that script is back."
            )
            self._set_sensitive(False)
            self.clash.set_visible(False)
            return

        self._set_sensitive(True)
        # When everything works, naming the file it was written to is data
        # nobody acts on, so it only appears when the file is the problem.
        self.knob_note.set_visible(False)

        for assignment in lpd8map.ASSIGNMENTS:
            row = self._knob_rows[assignment.const]
            cc = knobs.get(assignment.const)
            knob = lpd8map.cc_to_knob(cc) if cc is not None else None
            if knob is not None:
                row.show_choice(knob - 1)
            else:
                extra = f"CC {cc}" if cc is not None else "not set"
                row.show_choice(len(lpd8map.KNOBS), extra)

        for pad_slot in lpd8map.PAD_SLOTS:
            row = self._pad_rows[pad_slot.slot]
            pad = pads.get(pad_slot.slot)
            if pad is not None:
                row.show_choice(pad - 1)
            else:
                row.show_choice(len(lpd8map.PADS), "not set")

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

    def _set_sensitive(self, sensitive: bool) -> None:
        for row in (*self._knob_rows.values(), *self._pad_rows.values()):
            row.drop.set_sensitive(sensitive)

    # -- writing ---------------------------------------------------------
    def _on_knob_changed(self, drop: Gtk.DropDown, _param) -> None:
        const = drop.get_name().removeprefix("knob:")
        knob = int(drop.get_selected()) + 1
        if knob not in lpd8map.KNOBS:
            return
        self._write(lambda: lpd8map.set_knob(const, knob))

    def _on_pad_changed(self, drop: Gtk.DropDown, _param) -> None:
        slot = int(drop.get_name().removeprefix("pad:"))
        pad = int(drop.get_selected()) + 1
        if pad not in lpd8map.PADS:
            return
        self._write(lambda: lpd8map.set_pad(slot, pad))

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
