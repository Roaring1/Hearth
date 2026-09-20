"""Hearth Setup: the surfaces the everyday mixer deliberately leaves out.

The mixer is the small thing that is always open, so nothing in this module
is imported, built or polled until the user asks for Setup, and the polling
stops again the moment the window is closed. Opening Setup once must not
make the resident mixer permanently more expensive -- that is the whole
constraint this file is written under.

What folded in from the old GTK3 window, and what did not:

* The eighteen-row SERVICES grid became :func:`hearth.units.problems`. The
  window says what is *wrong*; a wall of green lamps answers a question
  nobody asked and hides the one row that matters.
* QUICK ACTIONS kept the two buttons that fix a stuck rig and dropped the
  ones that duplicated the mixer, the keyboard or each other.
* The LPD8 expander became a read of the *running* map, including the
  conflicts the old window computed and then never showed.
* Headset target and loopback latency folded in as they were: they are
  real settings for real hardware.
* SIGNAL FLOW was a diagram typed into the source, so it described the
  rig of the day it was written. It folded in as lines read from the
  router config, next to the MOONLIGHT MIC chooser it explains.
* PADFIRE telemetry, the Vesktop panel, the keyboard cheat-sheet, the
  second copy of the mixer and the app-settings page did not fold in.
"""

from __future__ import annotations

import logging
import threading

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import GLib, Gtk  # noqa: E402 - must follow require_version

from hearth import lpd8map  # noqa: E402
from hearth import mic_routes  # noqa: E402
from hearth import services as services_mod  # noqa: E402
from hearth import units as units_mod  # noqa: E402

log = logging.getLogger(__name__)

#: How often the window re-reads systemd *while it is on screen*. Slow on
#: purpose: this is a window somebody opened to fix something, not a
#: dashboard, and every pass is two ``systemctl`` calls.
POLL_S = 5

#: The unit the routing daemon reads its conf from, restarted on save.
ROUTES_UNIT = "roaring-audio-routesd.service"

LPD8_UNIT = lpd8map.UNIT


def _knob_name(cc: int) -> str:
    """``K3`` when a knob sends that CC, otherwise the CC number itself."""
    knob = lpd8map.cc_to_knob(cc)
    return f"K{knob}" if knob else f"CC {cc}"


def _tag(text: str) -> Gtk.Label:
    label = Gtk.Label(label=text, xalign=0.0)
    label.add_css_class("setup-tag")
    return label


def _note(text: str) -> Gtk.Label:
    label = Gtk.Label(label=text, xalign=0.0)
    label.add_css_class("setup-note")
    label.set_wrap(True)
    return label


class SetupWindow(Gtk.Window):
    """One scrolling column: what is wrong, the hardware, the controller."""

    def __init__(self, parent: Gtk.Window) -> None:
        super().__init__(title="Hearth Setup", transient_for=parent)
        self.add_css_class("hearth")
        # Height follows the content, like the mixer: a fixed 520 left a
        # slab of empty window under three short sections.
        self.set_default_size(430, -1)
        # Kept alive but hidden: building it twice would undo the point of
        # building it late. Hidden means unmapped, which stops the poll.
        self.set_hide_on_close(True)

        self._poll_id = 0
        self._scanning = False
        self._units: list[units_mod.Unit] = []
        self._fault_rows: dict[str, Gtk.Widget] = {}

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

        column.append(self._build_verdict())
        column.append(self._build_devices())
        column.append(self._build_mic_path())
        column.append(self._build_controller())
        column.append(self._build_everything())

        self.connect("map", self._on_map)
        self.connect("unmap", self._on_unmap)

    # -- the focal block -------------------------------------------------
    def _build_verdict(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)

        self.verdict = Gtk.Label(label="checking the rig\u2026", xalign=0.0)
        self.verdict.add_css_class("setup-verdict")
        self.verdict.set_wrap(True)
        box.append(self.verdict)

        self.faults = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.append(self.faults)

        self.reset_failed = Gtk.Button(label="Clear the failed state")
        self.reset_failed.add_css_class("setup-act")
        self.reset_failed.set_halign(Gtk.Align.START)
        self.reset_failed.set_tooltip_text(
            "systemctl --user reset-failed. Use after a unit has been fixed\n"
            "but systemd still refuses to start it."
        )
        self.reset_failed.connect("clicked", self._on_reset_failed)
        self.reset_failed.set_visible(False)
        box.append(self.reset_failed)

        return box

    def _on_reset_failed(self, _button: Gtk.Button) -> None:
        failed = [u.name for u in self._units if u.failed]
        if failed:
            services_mod.reset_failed(*failed)
            GLib.timeout_add_seconds(2, self._refresh_once)

    def _on_restart(self, _button: Gtk.Button, unit: str) -> None:
        services_mod.restart(unit)
        # systemd is asynchronous, so re-read rather than claim success.
        GLib.timeout_add_seconds(3, self._refresh_once)

    # -- hardware --------------------------------------------------------
    def _build_devices(self) -> Gtk.Widget:
        from hearth import legacy_core as core  # deliberately late: see the docstring

        self._core = core
        conf = core.read_conf(core.MXCONF)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.append(_tag("HEADSET"))

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.astro_chat = Gtk.CheckButton(label="Chat")
        self.astro_game = Gtk.CheckButton(label="Game")
        self.astro_game.set_group(self.astro_chat)
        is_game = "game" in conf.get("ASTRO_TARGET", "")
        self.astro_game.set_active(is_game)
        self.astro_chat.set_active(not is_game)
        row.append(self.astro_chat)
        row.append(self.astro_game)
        box.append(row)
        box.append(
            _note(
                "Which of the A50's two endpoints the buses feed. The headset "
                "mixes both, so the wrong one is quiet, not silent."
            )
        )

        lat_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        lat_row.append(Gtk.Label(label="Loopback latency", xalign=0.0))
        self.latency = Gtk.SpinButton.new_with_range(1, 200, 1)
        try:
            self.latency.set_value(int(conf.get("LATENCY_MSEC", "12")))
        except ValueError:
            self.latency.set_value(12)
        lat_row.append(self.latency)
        lat_row.append(Gtk.Label(label="ms"))
        box.append(lat_row)

        self.save_devices = Gtk.Button(label="Save and restart routing")
        self.save_devices.add_css_class("setup-act")
        self.save_devices.set_halign(Gtk.Align.START)
        self.save_devices.connect("clicked", self._on_save_devices)
        box.append(self.save_devices)

        return box

    def _on_save_devices(self, _button: Gtk.Button) -> None:
        core = self._core
        target = core.ASTRO_GAME if self.astro_game.get_active() else core.ASTRO_CHAT
        core.write_conf_key(core.MXCONF, "ASTRO_TARGET", target)
        core.write_conf_key(core.MXCONF, "LATENCY_MSEC", str(int(self.latency.get_value())))
        services_mod.restart(ROUTES_UNIT)
        self.save_devices.set_label("Saved \u2014 routing restarting")
        GLib.timeout_add_seconds(4, self._devices_settled)

    def _devices_settled(self) -> bool:
        self.save_devices.set_label("Save and restart routing")
        return False

    # -- where the mic ends up -------------------------------------------
    def _build_mic_path(self) -> Gtk.Widget:
        """The old SIGNAL FLOW panel, read from the config instead of typed.

        The mixer already owns *choosing* what feeds a bus -- that is the
        IN button on each mic strip. What it cannot show without growing a
        panel is the whole path at once, which is the thing you want when
        the person on the other end says they cannot hear you.
        """
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.append(_tag("MIC PATH"))

        self.flow = _note("")
        box.append(self.flow)

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        row.append(Gtk.Label(label="Moonlight sends", xalign=0.0))
        self.ml_stream = Gtk.CheckButton(label="Stream")
        self.ml_chat = Gtk.CheckButton(label="Chat")
        self.ml_chat.set_group(self.ml_stream)
        source = self._core.moonlight_mic_source()
        self.ml_stream.set_active(source == "b1_mic")
        self.ml_chat.set_active(source != "b1_mic")
        # Connected after the initial state is set, and each handler acts
        # only on the button that switched *on*, so one click is one write
        # and one service restart rather than two of each.
        self.ml_stream.connect("toggled", self._on_moonlight, "b1_mic")
        self.ml_chat.connect("toggled", self._on_moonlight, "b2_mic")
        row.append(self.ml_stream)
        row.append(self.ml_chat)
        box.append(row)

        self.ml_note = _note("")
        box.append(self.ml_note)
        return box

    def _on_moonlight(self, button: Gtk.CheckButton, source: str) -> None:
        if not button.get_active():
            return
        self.ml_note.set_text("Restarting the Moonlight mic\u2026")
        # daemon-reload plus a restart is a second or two of blocking work;
        # doing it on the main loop freezes the window mid-click.
        threading.Thread(target=self._set_moonlight, args=(source,), daemon=True).start()

    def _set_moonlight(self, source: str) -> None:
        try:
            self._core.set_moonlight_mic(source)
        except Exception as exc:  # pragma: no cover - a missing unit is not a crash
            log.warning("setup: could not point moonlight at %s: %s", source, exc)
        GLib.idle_add(self._moonlight_done)

    def _moonlight_done(self) -> bool:
        self.ml_note.set_text("")
        self._refresh_flow()
        return False

    def _refresh_flow(self) -> None:
        lines = mic_routes.flow_lines(
            mic_routes.read(),
            moonlight_source=self._core.moonlight_mic_source(),
            laptop_host=getattr(self._core, "LAPTOP_HOST", ""),
        )
        self.flow.set_text("\n".join(lines))

    # -- controller ------------------------------------------------------
    def _build_controller(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.append(_tag("LPD8"))

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.lpd8_state = Gtk.Label(label="\u2026", xalign=0.0)
        self.lpd8_state.set_hexpand(True)
        row.append(self.lpd8_state)
        reconnect = Gtk.Button(label="Reconnect")
        reconnect.add_css_class("setup-act")
        reconnect.set_tooltip_text(
            "Restart lpd8-mixer. Plugging the pad back in restarts it on its "
            "own; this is for when it did not."
        )
        reconnect.connect("clicked", self._on_restart, LPD8_UNIT)
        row.append(reconnect)
        remap = Gtk.Button(label="Remap\u2026")
        remap.add_css_class("setup-act")
        remap.set_tooltip_text(
            "Open the LPD8 window: which knob moves which bus, and what each pad does."
        )
        remap.connect("clicked", self._on_remap)
        row.append(remap)
        box.append(row)

        # The map is read from the running script rather than printed from a
        # table in the source, because the table in the source was wrong.
        self.lpd8_map = _note("")
        box.append(self.lpd8_map)
        self.lpd8_clash = Gtk.Label(label="", xalign=0.0)
        self.lpd8_clash.add_css_class("setup-clash")
        self.lpd8_clash.set_wrap(True)
        self.lpd8_clash.set_visible(False)
        box.append(self.lpd8_clash)

        return box

    def _on_remap(self, _button: Gtk.Button) -> None:
        """Hand off to the mixer, which owns the single LPD8 window.

        Opening a second copy from here would let two windows write the
        same script, so this asks the parent rather than building one --
        and only builds its own if Setup somehow has no mixer behind it.
        """
        opener = getattr(self.get_transient_for(), "open_remap", None)
        if opener is not None:
            opener()
            return
        from hearth.ui.gtk4.remap import RemapWindow

        RemapWindow(self).present()

    def _refresh_controller(self) -> None:
        try:
            mapping = lpd8map.read_map()
        except Exception:  # pragma: no cover - a missing script is not a crash
            mapping = {}
        if not mapping:
            self.lpd8_map.set_text("No map found in ~/bin/lpd8_mixer.sh.")
            self.lpd8_clash.set_visible(False)
            return
        labels = {a.const: a.label for a in lpd8map.ASSIGNMENTS}
        self.lpd8_map.set_text(
            "   ".join(
                f"{_knob_name(cc)} \u2192 {labels.get(const, const)}"
                for const, cc in sorted(mapping.items(), key=lambda kv: kv[1])
            )
        )
        clashes = lpd8map.conflicts(mapping)
        if clashes:
            # The old window computed this and threw it away, so one knob on
            # two buses just looked like a broken fader.
            lines = [
                f"{_knob_name(cc)} drives "
                + " and ".join(labels.get(const, const) for const in consts)
                for cc, consts in sorted(clashes.items())
            ]
            self.lpd8_clash.set_text("; ".join(lines))
        self.lpd8_clash.set_visible(bool(clashes))

    # -- the long list, behind a disclosure ------------------------------
    def _build_everything(self) -> Gtk.Widget:
        self.everything = Gtk.Expander(label="Every unit")
        self.everything.add_css_class("setup-note")
        self.all_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.all_list.set_margin_top(6)
        self.everything.set_child(self.all_list)
        # Nothing in here is laid out until it is asked for, and it is never
        # refreshed while it is shut.
        self.everything.connect("notify::expanded", lambda *_: self._fill_everything())
        return self.everything

    def _fill_everything(self) -> None:
        if not self.everything.get_expanded():
            return
        child = self.all_list.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.all_list.remove(child)
            child = nxt
        for unit in sorted(self._units, key=lambda u: u.name):
            line = Gtk.Label(label=unit.status_text, xalign=0.0)
            line.add_css_class("setup-note")
            if unit.healthy is False:
                line.add_css_class("bad")
            self.all_list.append(line)

    # -- polling, only while on screen -----------------------------------
    def _on_map(self, *_args) -> None:
        self._refresh_once()
        self._refresh_controller()
        # Read once per opening, not on the tick: the config only changes
        # when somebody changes it, and this window is not a monitor.
        self._refresh_flow()
        if not self._poll_id:
            self._poll_id = GLib.timeout_add_seconds(POLL_S, self._on_tick)

    def _on_unmap(self, *_args) -> None:
        if self._poll_id:
            GLib.source_remove(self._poll_id)
            self._poll_id = 0

    def _on_tick(self) -> bool:
        self._refresh_once()
        return True

    def _refresh_once(self) -> bool:
        """Read systemd off the main loop. Returns False so it can be a timeout."""
        if self._scanning:
            return False
        self._scanning = True
        threading.Thread(target=self._scan, daemon=True).start()
        return False

    def _scan(self) -> None:
        try:
            found = units_mod.state()
        except Exception as exc:  # pragma: no cover - a dead systemd is not a crash
            log.warning("setup: could not read units: %s", exc)
            found = []
        GLib.idle_add(self._apply, found)

    def _apply(self, found: list[units_mod.Unit]) -> bool:
        self._scanning = False
        self._units = found
        # Hearth's own unit is not a fault. The window saying "hearth ui is
        # stopped" while it is the thing on screen is nonsense: the user
        # closed the mixer once and started it by hand, and the unit is
        # allowed to be dead. It stays in the full list, honestly, but it
        # never counts as something wrong.
        problems = [u for u in units_mod.problems(found) if not u.name.startswith("hearth")]

        if not found:
            self.verdict.set_text("systemd did not answer")
            self.verdict.add_css_class("bad")
        elif problems:
            count = len(problems)
            self.verdict.set_text("1 thing is wrong" if count == 1 else f"{count} things are wrong")
            self.verdict.add_css_class("bad")
        else:
            self.verdict.set_text("The rig is up")
            self.verdict.remove_css_class("bad")

        wanted = {unit.name: unit for unit in problems}
        for name, row in list(self._fault_rows.items()):
            if name not in wanted:
                self.faults.remove(row)
                del self._fault_rows[name]
        for name, unit in wanted.items():
            row = self._fault_rows.get(name)
            if row is None:
                row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
                row.add_css_class("setup-row")
                text = Gtk.Label(xalign=0.0)
                text.set_hexpand(True)
                text.set_wrap(True)
                row.append(text)
                button = Gtk.Button(label="Restart")
                button.add_css_class("setup-act")
                button.connect("clicked", self._on_restart, name)
                row.append(button)
                row._text = text  # type: ignore[attr-defined]
                self.faults.append(row)
                self._fault_rows[name] = row
            row._text.set_text(unit.status_text)  # type: ignore[attr-defined]

        # An empty list means the scan failed, not that nothing is installed
        # -- the verdict above already says so. Claiming the pad is missing
        # because systemd was unreachable is the window lying about hardware
        # that is sitting right there.
        if not found:
            self.lpd8_state.set_text("unknown \u2014 systemd did not answer")
        else:
            pad = next((u for u in found if u.name == LPD8_UNIT), None)
            self.lpd8_state.set_text(pad.status_text if pad else "lpd8 mixer is not installed")

        self.reset_failed.set_visible(any(u.failed for u in problems))
        self._fill_everything()
        return False
