"""The Hearth mixer, GTK4.

This is the round-10 design as a running window and nothing else: six channel
strips in two groups, the share destination, a rail for minimised channels and
a drawer for per-app listeners. The hardware panel, services list, quick
actions, pad grid and log viewer are deliberately absent; they can come back as
separate windows once this one is right.

All audio work is delegated to the toolkit-free modules:

* :mod:`hearth.collector` - one background poll for sinks and streams
* :mod:`hearth.meters`    - ``parec`` peak levels
* :mod:`hearth.audio.pactl` - volume and mute writes
* :mod:`hearth.theme`     - the desktop palette
* :mod:`hearth.settings`  - window geometry and layout memory

The collector calls back on its own thread, so every snapshot is marshalled
onto the main loop with ``GLib.idle_add`` before a widget is touched.
"""

from __future__ import annotations

import logging
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")

from gi.repository import Gdk, GLib, Gtk  # noqa: E402 - must follow require_version

from hearth import collector as collector_mod  # noqa: E402
from hearth import control  # noqa: E402
from hearth import ipc as ipc_mod  # noqa: E402
from hearth import leds  # noqa: E402
from hearth import meters as meters_mod  # noqa: E402
from hearth import mic_routes  # noqa: E402
from hearth import services as services_mod  # noqa: E402
from hearth import settings as settings_mod  # noqa: E402
from hearth.audio import pactl  # noqa: E402
from hearth.ui.gtk4 import icons as icons_mod  # noqa: E402
from hearth.ui.gtk4 import style as style_mod  # noqa: E402
from hearth.ui.gtk4.widgets import Fader, Meter, Scale, Sliver, parse_rgb, rounded  # noqa: E402

log = logging.getLogger(__name__)

#: Hearth's own icon set, drawn by scripts/draw_art.py. Theme icons vary
#: wildly between icon packs; these do not.
ART_DIR = Path(__file__).resolve().parents[4] / "assets" / "art"

APP_ID = "co.roaring.Hearth"

#: Meter height, expanded and compact. Everything else in a strip is fixed, so
#: this single number is what "show apps" actually changes.
METER_TALL = 96
METER_SHORT = 72

#: How long the laptop input may sit silent and unused before it folds
#: itself into the rail. It is a guest input, not a permanent strip.
IDLE_COLLAPSE_S = 180.0

#: How long a fader stays "held" after the user touches it, so an in-flight
#: snapshot cannot yank the cap back under their finger.
HOLD_S = 1.2

#: The redraw loop idles slow and speeds up while something is being used.
#: 15 Hz is invisible on a window nobody is touching and costs almost
#: nothing; 60 Hz for a second under the hand is what makes a dragged
#: fader feel attached to the pointer instead of trailing it.
FRAME_IDLE_MS = 66
FRAME_BUSY_MS = 16
BUSY_S = 1.0


@dataclass(frozen=True)
class Channel:
    """One strip's fixed facts. Real names only - never B1, B2 or SM7B."""

    name: str
    sink: str
    group: str
    bind: str = ""
    pad: str = ""


#: The mixer's channels, in the order the design shows them. Stream is a mic
#: bus that is hidden by default because it is set once and left alone.
CHANNELS: tuple[Channel, ...] = (
    Channel("Game", "vm_game", "headset", "K7", "P7"),
    Channel("Chat", "vm_chat", "headset", "K6", "P2"),
    Channel("Music", "vm_music", "headset", "K5", "P1"),
    Channel("Laptop", "laptop_audio", "headset"),
    # B1 is the microphone: 2i2 -> Carla -> this bus -> every app that
    # takes mic input, Discord included. Calling it "Stream mic" implied
    # a stream that does not exist and implied Discord was somewhere
    # else. B2 is the spare bus, hidden until it is used.
    Channel("Mic B1", "mic_b1", "mic", "K8", "P6"),
    Channel("Mic B2", "mic_b2", "mic"),
)

#: The systemd user unit that creates each bus. A banner that names a
#: missing bus but cannot restart the thing that makes it is just nagging.
UNIT_FOR_SINK: dict[str, str] = {
    "vm_game": "roaring-vm-sinks.service",
    "vm_chat": "roaring-vm-sinks.service",
    "vm_music": "roaring-vm-sinks.service",
    "mic_b1": "roaring-mic-busses.service",
    "mic_b2": "roaring-mic-busses.service",
    "laptop_audio": "roaring-laptop-audio.service",
}

#: Everywhere a bus can be sent. A bus is a null sink, so "output" means
#: a ``module-loopback`` from its monitor into a real device. Naming them
#: here keeps the popover honest: nothing appears that cannot be wired.
#: Capture devices that can be poured into a mic bus. Until now a mic bus
#: could only ever be whatever Carla happened to be feeding it, so the
#: headset's own boom mic had no way in at all.
#: ``sm7b_mono`` is the tap *before* Carla, so calling it "SM7B (Carla)"
#: described the wrong end of the chain -- the processed feed is the
#: separate "SM7B via Carla" entry. ``astro_mic_48k`` rather than the raw
#: ALSA node because that is the resampled source the router daemon owns.
MIC_SOURCES: tuple[tuple[str, str], ...] = (
    ("SM7B (raw)", "sm7b_mono"),
    ("A50 boom mic", "astro_mic_48k"),
    (
        "Scarlett in",
        "alsa_input.usb-Focusrite_Scarlett_Solo_USB_Y7XZGYX15C77AB-00.Direct__Direct__source",
    ),
    ("LifeCam", "alsa_input.usb-Microsoft_Microsoft___LifeCam_Studio_TM_-02.mono-fallback"),
)

OUTPUTS: tuple[tuple[str, str], ...] = (
    ("A50 game", "alsa_output.usb-Astro_Gaming_Astro_A50-00.stereo-game"),
    ("A50 chat", "alsa_output.usb-Astro_Gaming_Astro_A50-00.stereo-chat"),
    (
        "Scarlett",
        "alsa_output.usb-Focusrite_Scarlett_Solo_USB_Y7XZGYX15C77AB-00.Direct__Direct__sink",
    ),
    ("Share", "vm_share"),
)

#: The A50 presents two endpoints of one pair of speakers. Sending a bus
#: to both does not make it louder in two places -- it plays the same bus
#: into the same ears twice, out of phase with itself. Ticking one
#: endpoint therefore unticks the other.
EXCLUSIVE: dict[str, str] = {
    "alsa_output.usb-Astro_Gaming_Astro_A50-00.stereo-game": (
        "alsa_output.usb-Astro_Gaming_Astro_A50-00.stereo-chat"
    ),
    "alsa_output.usb-Astro_Gaming_Astro_A50-00.stereo-chat": (
        "alsa_output.usb-Astro_Gaming_Astro_A50-00.stereo-game"
    ),
}

#: Apps that actually appear on this desk get their own brand colour, defined
#: as a CSS class in the stylesheet. Anything else keeps the neutral mark.
APP_CLASSES = {
    "discord": "discord",
    "vesktop": "discord",
    "firefox": "firefox",
    "spotify": "spotify",
    "chrome": "chrome",
    "chromium": "chrome",
    "obs": "obs",
    "steam": "steam",
    "vlc": "vlc",
}


def _sources_safe() -> list:
    """Live capture devices, or nothing if pactl is unreachable."""
    try:
        return pactl.sources()
    except OSError:  # pragma: no cover - no pactl, no sources
        return []


def app_class(name: str) -> str:
    """The mark CSS class for an application name, or "" for the default."""
    key = (name or "").lower()
    for needle, cls in APP_CLASSES.items():
        if needle in key:
            return cls
    return ""


def load_css(provider: Gtk.CssProvider, css: str) -> None:
    """``load_from_data`` changed signature across GTK4 point releases."""
    try:
        provider.load_from_data(css, -1)
    except TypeError:
        provider.load_from_data(css.encode("utf-8"))


def _set_mute_icon(button: Gtk.Button, muted: bool) -> None:
    """Draw a speaker, not a typographic box.

    The old [ ] / [x] pair read as a checkbox, which invites the reading
    "tick this to include the app" - the opposite of what it does.
    """
    art = ART_DIR / ("app-muted@2x.png" if muted else "app-unmuted@2x.png")
    if art.exists():
        image = Gtk.Image.new_from_file(str(art))
    else:  # pragma: no cover - a source checkout always has the art
        name = "audio-volume-muted-symbolic" if muted else "audio-volume-high-symbolic"
        image = Gtk.Image.new_from_icon_name(name)
    image.set_pixel_size(12)
    button.set_child(image)
    button.set_tooltip_text("Unmute this app" if muted else "Mute this app")


@dataclass
class Listener:
    """One application playing into a bus."""

    name: str
    muted: bool = False
    live: bool = True
    #: PulseAudio sink-input index, needed to mute this app alone.
    index: str = ""


@dataclass
class ChannelState:
    """What the window knows about a channel right now."""

    volume: int = 0
    muted: bool = False
    present: bool = False
    device: str = ""
    #: False until the first snapshot lands, so the initial jump from 0
    #: to the real volume is not mistaken for someone turning a knob.
    seen: bool = False
    listeners: list[Listener] = field(default_factory=list)


class Strip(Gtk.Box):
    """One channel: header, app marks, meter and fader, value, mute, device."""

    def __init__(self, window: MixerWindow, channel: Channel) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        self.win = window
        self.channel = channel
        self.pal = window.pal
        self.add_css_class("strip")
        self.set_size_request(108, -1)
        # Without this the lone strip in a group swallows every spare
        # pixel in the window and ends up three times its neighbours.
        self.set_hexpand(False)
        self.set_halign(Gtk.Align.START)
        self.set_valign(Gtk.Align.START)
        self._held_until = 0.0
        self._hw_until = 0.0
        # Destination checkboxes, kept so one endpoint can untick another.
        self._out_checks: dict[str, Gtk.CheckButton] = {}
        self._routing = False

        # header: grip, name, minimise
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        grip = self._grip()
        header.append(grip)
        # Single-word bus names shout; a two-word name like "Discord mic"
        # reads better as typed and would not fit the strip uppercased.
        label = channel.name.upper() if " " not in channel.name else channel.name
        name = Gtk.Label(label=label, xalign=0.0)
        name.add_css_class("strip-name")
        # No ellipsize: a strip should grow to fit its own name rather
        # than clip it, so "Discord mic" stays readable.
        name.set_hexpand(True)
        name.set_tooltip_text(channel.name)
        header.append(name)
        minimise = Gtk.Button(label="\u2013")
        minimise.add_css_class("minbox")
        minimise.set_tooltip_text(f"Minimise {channel.name} to the rail")
        minimise.connect("clicked", lambda _b: window.minimise(channel.sink))
        header.append(minimise)
        # Double clicking a bus header collapses it, the same way a
        # titlebar double click does everywhere else on this desktop.
        hclick = Gtk.GestureClick()
        hclick.connect(
            "pressed",
            lambda _g, n, *_a: window.minimise(channel.sink) if n >= 2 else None,
        )
        header.add_controller(hclick)
        self.append(header)

        # No icon row: every app already shows its icon on its own row
        # below, so the strip was drawing the same icons twice.

        # meter + fader
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=3)
        row.set_halign(Gtk.Align.CENTER)
        self.meter = Meter(self.pal, height=window.meter_h)
        self.fader = Fader(self.pal, height=window.meter_h)
        self.fader.connect("moved", self._on_moved)
        # The ruler used to be drawn once per group, hanging off the right
        # edge where it read as clipped and belonged to nothing. One per
        # strip, inside the strip, is what the design actually shows.
        self.scale = Scale(self.pal, window.meter_h)
        row.append(self.meter)
        row.append(self.fader)
        row.append(self.scale)
        self.append(row)

        # value + bind chip
        vrow = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self.value = Gtk.Label(label="--", xalign=0.0)
        self.value.add_css_class("value")
        self.value.set_hexpand(True)
        vrow.append(self.value)
        # The bind chip was a readout of something the user could only
        # change by editing a shell script. It is now the control: click
        # it and pick the knob that should drive this bus.
        self.bind_button = None
        if channel.bind:
            chip = Gtk.Button(label=channel.bind)
            chip.add_css_class("bind")
            chip.set_valign(Gtk.Align.CENTER)
            chip.set_tooltip_text(self._bind_tip())
            chip.connect("clicked", lambda _b: self.win.open_remap())
            self.bind_button = chip
            vrow.append(chip)
        else:
            # A bus with no knob still owes the row the chip's height, or
            # its strip ends higher than every other strip.
            spacer = Gtk.Label(label="K0")
            spacer.add_css_class("bind")
            spacer.add_css_class("empty")
            spacer.set_valign(Gtk.Align.CENTER)
            spacer.set_can_target(False)
            vrow.append(spacer)
        self.append(vrow)

        # mute + send
        brow = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self.mute = Gtk.ToggleButton(label="MUTE")
        self.mute.add_css_class("mute")
        self.mute.set_hexpand(True)
        self._mute_handler = self.mute.connect("toggled", self._on_mute)
        brow.append(self.mute)
        self.append(brow)

        # outputs: where this bus is sent, and for a mic bus, whether
        # you can hear yourself. Both are the same mechanism.
        self.out_button = Gtk.MenuButton()
        self.out_button.set_label("OUT")
        self.out_button.add_css_class("outbtn")
        self.out_button.set_tooltip_text(f"Choose where {channel.name} is sent")
        self.out_popover = Gtk.Popover()
        self.out_popover.add_css_class("outpop")
        self.out_button.set_popover(self.out_popover)
        self.out_popover.connect("show", lambda _p: self._fill_outputs())
        brow.append(self.out_button)

        # A mic bus also needs to say what feeds it. Everything else in
        # this window routes sound outwards; a mic is the one direction
        # the mixer never offered.
        self.in_button: Gtk.MenuButton | None = None
        if channel.group == "mic":
            self.in_button = Gtk.MenuButton()
            self.in_button.set_label("IN")
            self.in_button.add_css_class("outbtn")
            self.in_button.set_tooltip_text(f"Choose what feeds {channel.name}")
            self.in_popover = Gtk.Popover()
            self.in_popover.add_css_class("outpop")
            self.in_button.set_popover(self.in_popover)
            self.in_popover.connect("show", lambda _p: self._fill_inputs())
            brow.append(self.in_button)

        # device line
        self.device = Gtk.Label(label="\u2026", xalign=0.0)
        self.device.set_tooltip_text(f"Where {channel.name} lands")
        self.device.add_css_class("device")
        self.device.set_ellipsize(3)  # PANGO_ELLIPSIZE_END
        self.append(self.device)

        # listeners, expanded only
        self.listeners = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.append(self.listeners)
        self.listeners.set_visible(window.expanded)

        self._add_drag(grip)

    # -- construction helpers -------------------------------------------
    def _grip(self) -> Gtk.DrawingArea:
        area = Gtk.DrawingArea()
        area.set_content_width(10)
        area.set_content_height(12)
        area.set_tooltip_text("Drag to reorder")

        def draw(_a, cr, w, h):
            cr.set_source_rgb(*parse_rgb(self.pal["button"]))
            for col in range(2):
                for rowi in range(3):
                    cr.arc(3 + col * 4, 3 + rowi * 3.5, 1.1, 0, 6.2832)
                    cr.fill()

        area.set_draw_func(draw)
        return area

    def _bind_tip(self) -> str:
        pad = f", pad {self.channel.pad}" if self.channel.pad else ""
        return f"LPD8 knob {self.channel.bind}{pad} \u2014 click to remap the controller"

    def _add_drag(self, handle: Gtk.Widget) -> None:
        # The drag source used to sit on the whole strip, so a press on
        # the fader started a strip drag and the fader never saw the
        # motion. Only the grip drags the strip now.
        source = Gtk.DragSource()
        source.set_actions(Gdk.DragAction.MOVE)
        source.connect(
            "prepare",
            lambda *_a: Gdk.ContentProvider.new_for_value(self.channel.sink),
        )
        handle.add_controller(source)
        target = Gtk.DropTarget.new(str, Gdk.DragAction.MOVE)
        target.connect("drop", self._on_drop)
        self.add_controller(target)

    def _on_drop(self, _t: Gtk.DropTarget, value: str, _x: float, _y: float) -> bool:
        payload = str(value)
        if payload.startswith("app:"):
            # An application landed on this strip: move its stream here
            # rather than reordering anything.
            pactl.move_sink_input(payload[4:], self.channel.sink)
            GLib.timeout_add(400, self._route_settled)
            return True
        self.win.reorder(payload, self.channel.sink)
        return True

    # -- user input ------------------------------------------------------
    def _on_moved(self, _f: Fader, value: int) -> None:
        self._held_until = time.monotonic() + HOLD_S
        self.win.quicken()
        self.value.set_text(f"{value}")
        pactl.set_volume(self.channel.sink, value)

    def _on_mute(self, button: Gtk.ToggleButton) -> None:
        active = button.get_active()
        button.set_label("MUTED" if active else "MUTE")
        pactl.set_mute(self.channel.sink, active)

    # -- state -----------------------------------------------------------
    @property
    def held(self) -> bool:
        """True while the user's own drag owns this fader."""
        return time.monotonic() <= self._held_until

    def mark_hardware(self, seconds: float = 2.5) -> None:
        """Light the cap: something outside this window moved the fader.

        The LPD8 knobs write straight to PulseAudio, so the only honest
        signal available here is a volume that moved while nobody was
        touching the widget.
        """
        self._hw_until = time.monotonic() + seconds
        self.fader.set_hardware(True)

    def tick_hardware(self) -> None:
        """Let the accent fade once the hardware stops driving."""
        if self._hw_until and time.monotonic() > self._hw_until:
            self._hw_until = 0.0
            self.fader.set_hardware(False)

    def set_meter_height(self, height: int) -> None:
        self.meter.set_content_height(height)
        self.fader.set_content_height(height)
        self.scale.set_meter_height(height)

    def set_expanded(self, expanded: bool) -> None:
        self.listeners.set_visible(expanded)

    def apply(self, state: ChannelState) -> None:
        dead = not state.present
        self.set_css_state(dead)
        self.meter.set_state(muted=state.muted, dead=dead)
        self.set_muted_look(state.muted and not dead)
        self.fader.set_dead(dead)
        if not dead and time.monotonic() > self._held_until:
            self.fader.set_value(state.volume)
            self.value.set_text(f"{state.volume}")
        if dead:
            self.value.set_text("--")
        self.mute.handler_block(self._mute_handler)
        self.mute.set_active(state.muted and not dead)
        self.mute.set_label("MUTED" if (state.muted and not dead) else "MUTE")
        self.mute.set_sensitive(not dead)
        self.mute.handler_unblock(self._mute_handler)

        self.device.remove_css_class("missing")
        if dead:
            self.device.add_css_class("missing")
            self.device.set_text("no device")
        else:
            self.device.set_text(state.device or "\u2014")

        self._fill_listeners(state.listeners)

    def set_muted_look(self, muted: bool) -> None:
        """Tint the whole strip while its bus is muted.

        A muted bus is a state of the whole channel, not of one button,
        so the channel is what changes colour.
        """
        if muted:
            self.add_css_class("muted-bus")
        else:
            self.remove_css_class("muted-bus")

    def set_css_state(self, dead: bool) -> None:
        if dead:
            self.add_css_class("dead")
        else:
            self.remove_css_class("dead")

    def _clear(self, box: Gtk.Box) -> None:
        child = box.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            box.remove(child)
            child = nxt

    def _mark(self, app: str, size: int) -> Gtk.Widget:
        """The installed icon for an app, falling back to a letter chip."""
        image = icons_mod.icon_image(app, size)
        if image is not None:
            image.set_valign(Gtk.Align.CENTER)
            return image
        chip = Gtk.Label(label=app[:1].upper() or "?")
        chip.add_css_class("mark")
        chip.set_valign(Gtk.Align.CENTER)
        chip.set_tooltip_text(app)
        brand = app_class(app)
        if brand:
            chip.add_css_class(brand)
        return chip

    def _fill_outputs(self) -> None:
        """Build the routing list from the live module table.

        Read at open time rather than cached: somebody else's script can
        load or unload a loopback at any moment, and a checkbox that
        disagrees with the graph is worse than no checkbox.
        """
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        source = f"{self.channel.sink}.monitor"
        try:
            live = {loop.sink for loop in pactl.select_loopbacks(pactl.modules(), source=source)}
        except Exception:  # pragma: no cover - a dead server must not crash the UI
            live = set()
        if self.channel.group == "mic":
            hint = Gtk.Label(
                label="2i2 \u2192 Carla \u2192 this bus. Tick a device to hear it.",
                xalign=0.0,
            )
            hint.add_css_class("outhint")
            hint.set_wrap(True)
            hint.set_max_width_chars(28)
            box.append(hint)
        self._out_checks = {}
        for label, sink in OUTPUTS:
            if sink == self.channel.sink:
                continue
            check = Gtk.CheckButton(label=label)
            check.set_active(sink in live)
            check.connect("toggled", self._on_route, sink)
            self._out_checks[sink] = check
            box.append(check)
        self.out_popover.set_child(box)

    def _fill_inputs(self) -> None:
        """Which capture devices are poured into this mic bus."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        hint = Gtk.Label(label="Feeding this mic bus", xalign=0.0)
        hint.add_css_class("outhint")
        box.append(hint)
        live = {source for source, sinks in self.win._routes.items() if self.channel.sink in sinks}
        present = {entry.name for entry in _sources_safe()}
        conf = mic_routes.read()

        # Carla's processed chain comes first because on this rig it is the
        # microphone: the raw entries below it are the pre-Carla taps. It
        # used to be wired into both mic buses at login with no way to say
        # no, so B2 was a copy of B1 whether or not anyone asked.
        carla = Gtk.CheckButton(label="SM7B via Carla")
        carla.set_active(mic_routes.carla_enabled(conf, self.channel.sink))
        carla.set_tooltip_text("2i2 \u2192 Carla \u2192 this bus")
        carla.connect("toggled", self._on_carla_in)
        box.append(carla)

        for label, source in MIC_SOURCES:
            check = Gtk.CheckButton(label=label)
            known = source in mic_routes.RAW_TOKEN
            check.set_active(
                mic_routes.raw_enabled(conf, self.channel.sink, source) if known else source in live
            )
            check.set_sensitive(source in present)
            if source not in present:
                check.set_tooltip_text("Not plugged in")
            check.connect("toggled", self._on_mic_in, source)
            box.append(check)
        self.in_popover.set_child(box)

    def _on_carla_in(self, check: Gtk.CheckButton) -> None:
        """Turn Carla's processed output into this bus on or off."""
        mic_routes.set_carla(self.channel.sink, check.get_active())
        GLib.timeout_add(400, self._route_settled)

    def _on_mic_in(self, check: Gtk.CheckButton, source: str) -> None:
        """Wire or unwire one capture device into this mic bus.

        For the two sources the router daemon manages this writes the
        config and lets the daemon converge -- doing it with ``pactl``
        here looked like it worked and was undone three seconds later.
        Anything else is a plain loopback, which nothing else touches.
        """
        wanted = check.get_active()
        if source in mic_routes.RAW_TOKEN:
            conf = mic_routes.read()
            mic_routes.write(mic_routes.raw_edits(conf, self.channel.sink, source, wanted))
            GLib.timeout_add_seconds(4, self._route_settled)
            return
        if wanted:
            pactl.load_loopback(
                source,
                self.channel.sink,
                latency_msec=int(self.win.cfg.get("latency_msec", 12) or 12),
            )
        else:
            pactl.unload_loopbacks(source, [self.channel.sink])
        GLib.timeout_add(400, self._route_settled)

    def _on_route(self, check: Gtk.CheckButton, sink: str) -> None:
        """Wire or unwire one destination for this bus."""
        if self._routing:
            # A partner checkbox is being corrected; its toggle is not a
            # user decision and must not re-enter this handler.
            return
        source = f"{self.channel.sink}.monitor"
        if check.get_active():
            pactl.load_loopback(
                source, sink, latency_msec=int(self.win.cfg.get("latency_msec", 12) or 12)
            )
            partner = EXCLUSIVE.get(sink)
            if partner:
                # Both A50 endpoints were ending up ticked, so a bus played
                # into the same ears twice. Choosing one endpoint is
                # choosing, not adding.
                pactl.unload_loopbacks(source, [partner])
                widget = self._out_checks.get(partner)
                if widget is not None and widget.get_active():
                    self._routing = True
                    try:
                        widget.set_active(False)
                    finally:
                        self._routing = False
        else:
            pactl.unload_loopbacks(source, [sink])
        GLib.timeout_add_seconds(1, self._route_settled)

    def _route_settled(self) -> bool:
        self.win.collector.refresh_now()
        return False

    def _on_app_mute(self, button: Gtk.Button, listener: Listener) -> None:
        """Mute one app without touching the bus everything else rides on."""
        muted = not listener.muted
        pactl.set_stream_mute(listener.index, muted)
        # Paint immediately; the next snapshot confirms it.
        listener.muted = muted
        _set_mute_icon(button, muted)
        button.set_tooltip_text(f"Unmute {listener.name}" if muted else f"Mute {listener.name}")
        if muted:
            button.add_css_class("on")
        else:
            button.remove_css_class("on")

    def _fill_listeners(self, listeners: list[Listener]) -> None:
        self._clear(self.listeners)
        for listener in listeners[:5]:
            # Round 10 gives every app its own row: brand chip, name, and the
            # little mute box on the right, so one app can be silenced
            # without touching the bus everything else is riding on.
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
            mark = self._mark(listener.name, 12)
            if listener.index:
                # The whole row is the handle. A 12 px icon was a
                # pixel-hunt: miss it and the press did nothing, which
                # reads as a broken feature rather than a small target.
                # Grabbing anywhere on the row drags the app to another bus.
                grab = Gtk.DragSource()
                grab.set_actions(Gdk.DragAction.MOVE)
                grab.connect(
                    "prepare",
                    lambda *_a, idx=listener.index: Gdk.ContentProvider.new_for_value(f"app:{idx}"),
                )
                row.add_controller(grab)
                row.set_tooltip_text(f"{listener.name} \u2014 drag onto another bus")
            row.append(mark)
            name = Gtk.Label(label=listener.name, xalign=0.0)
            name.add_css_class("listener")
            name.set_ellipsize(3)
            # Cap the requested width: without this a long app name
            # like "gst-launch-1.0" makes its whole strip wider than
            # the others and the row of strips stops lining up.
            name.set_max_width_chars(10)
            name.set_width_chars(0)
            name.set_hexpand(True)
            name.set_tooltip_text(listener.name)
            if listener.muted:
                name.add_css_class("muted")
            row.append(name)
            box = Gtk.Button()
            box.add_css_class("appmute")
            _set_mute_icon(box, listener.muted)
            if listener.muted:
                box.add_css_class("on")
            box.set_tooltip_text(
                f"Unmute {listener.name}" if listener.muted else f"Mute {listener.name}"
            )
            box.set_sensitive(bool(listener.index))
            box.connect("clicked", self._on_app_mute, listener)
            row.append(box)
            self.listeners.append(row)
        if not listeners:
            # A mic bus is consumed, not played: nothing is "playing"
            # into it, and what matters is whether an app is listening.
            word = "nothing listening" if self.channel.group == "mic" else "nothing playing"
            empty = Gtk.Label(label=word, xalign=0.0)
            empty.add_css_class("listener")
            self.listeners.append(empty)


class MixerWindow(Gtk.ApplicationWindow):
    """The whole window. Groups on the left, share, then the minimise rail."""

    def __init__(self, app: Gtk.Application) -> None:
        super().__init__(application=app, title="Hearth")
        self.add_css_class("hearth")
        self.pal = app.pal
        self.cfg = settings_mod.load()
        # The app rows are the point of the mixer, so they are always on:
        # the drawer toggle that used to hide them is gone.
        self.expanded = True
        self.meter_h = METER_TALL
        self.hidden = set(self.cfg.get("mixer_hidden") or ["mic_b2"])
        # A bus whose device is unplugged is not a fault, it is an
        # absence: the laptop is only here sometimes. Absent buses fold
        # away on their own and come back when the sink does, and they
        # are deliberately not written to mixer_hidden -- hiding is the
        # user's choice, absence is the world's.
        self.absent: set[str] = set()
        #: source -> sinks, refreshed with every snapshot.
        self._routes: dict[str, set[str]] = {}
        #: destinations that are muted at the device itself
        self._muted_outs: set[str] = set()
        #: in-flight window resize, if any
        self._size_target: tuple[int, int] | None = None
        self._size_tick: int | None = None
        self._frame_ms = FRAME_IDLE_MS
        self._busy_until = 0.0
        # Only a bus that was working and then vanished is worth a
        # banner. One that was never there since launch is just unplugged.
        self.ever_present: set[str] = set()
        self.minimised = set(self.cfg.get("mixer_minimised") or [])
        self.order = self._load_order()
        self.states: dict[str, ChannelState] = {c.sink: ChannelState() for c in CHANNELS}
        self.strips: dict[str, Strip] = {}
        self.slivers: dict[str, Sliver] = {}
        self.group_tags: dict[str, Gtk.Label] = {}
        # Looked up lazily: the controller is often not plugged in, and
        # asking ALSA at startup would slow every launch for a lamp.
        self._led_port: str | None = None
        # Laptop audio is plugged in occasionally; when it has been
        # silent and unused for a while it should get out of the way
        # rather than hold a full strip open.
        self._laptop_busy = time.monotonic()

        # Width is remembered; height follows the content. A mixer with a
        # fixed strip height has one correct height, and restoring a taller
        # one just leaves a slab of empty window under the strips.
        # Both axes follow the content: a mixer has exactly one right size,
        # and a remembered width only ever reopens as empty grey.
        self.set_default_size(-1, -1)
        self.set_resizable(True)

        self.root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.root.set_valign(Gtk.Align.START)
        self.root.set_margin_top(5)
        self.root.set_margin_bottom(4)
        self.root.set_margin_start(5)
        self.root.set_margin_end(5)
        self.set_child(self.root)

        self.banner = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.banner.add_css_class("banner")
        self.banner_text = Gtk.Label(label="", xalign=0.0)
        self.banner_text.add_css_class("banner-text")
        self.banner_text.set_hexpand(True)
        self.banner.append(self.banner_text)
        # A fault nobody can act on is just nagging, so the banner
        # carries the one command that would fix it.
        self.banner_units: tuple[str, ...] = ()
        self.banner_button = Gtk.Button(label="RESTART")
        self.banner_button.add_css_class("banner-restart")
        self.banner_button.connect("clicked", self._on_restart)
        self.banner.append(self.banner_button)
        self.banner.set_visible(False)
        self.root.append(self.banner)

        self.body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        self.body.set_vexpand(False)
        self.body.set_valign(Gtk.Align.START)
        self.root.append(self.body)

        # Which edge minimised channels stack along. The stack is locked to
        # the edge -- vertical down the right, horizontal along the bottom --
        # so there is never a diagonal or wrapped pile to reason about.
        self.rail_dock = "bottom" if self.cfg.get("mixer_rail_dock") == "bottom" else "right"
        self.rail = Gtk.Box(spacing=3)
        # The old "min" caption was a label for a label. The same slot now
        # carries the one control the rail needs: which edge it sits on.
        self.rail_handle = Gtk.Button()
        self.rail_handle.add_css_class("rail-handle")
        self.rail_handle.connect("clicked", self._on_rail_dock)
        self.rail.append(self.rail_handle)
        self._apply_rail_dock()

        self.rebuild()
        self._start_backends()
        self.connect("close-request", self._on_close)

        # Setup is a second window that does not exist until it is asked
        # for. It gets no permanent button here: a mixer this size cannot
        # spare a control that is used once a month, so it is right-click
        # or Ctrl+comma, and the fault banner is what shouts when it does
        # matter.
        self._setup_window: Gtk.Window | None = None
        self._remap_window: Gtk.Window | None = None
        self._menu: Gtk.Popover | None = None
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key)
        self.add_controller(keys)
        right_click = Gtk.GestureClick(button=3)
        right_click.connect("pressed", self._on_right_click)
        self.add_controller(right_click)

    # -- layout ----------------------------------------------------------
    def _load_order(self) -> list[str]:
        saved = [s for s in (self.cfg.get("mixer_order") or []) if isinstance(s, str)]
        known = [c.sink for c in CHANNELS]
        ordered = [s for s in saved if s in known]
        ordered += [s for s in known if s not in ordered]
        return ordered

    def _channel(self, sink: str) -> Channel | None:
        for channel in CHANNELS:
            if channel.sink == sink:
                return channel
        return None

    def rebuild(self) -> None:
        """Rebuild the strip area from order, hidden and minimised state."""
        # One shape, always: groups side by side, rail locked to the right
        # edge. Round 19 flipped both axes once the window was taller than
        # wide -- and folding strips is exactly what made it taller than
        # wide, so minimising a few channels rewrote the whole layout under
        # the user's hand and left strips squeezed off the edge.
        # The rail may be parented to either the body or the root, so it is
        # detached by asking it where it is rather than assuming.
        rail_parent = self.rail.get_parent()
        if rail_parent is not None:
            rail_parent.remove(self.rail)
        child = self.body.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.body.remove(child)
            child = nxt
        self.strips.clear()
        self.slivers.clear()
        self.group_tags.clear()

        for group in ("headset", "mic"):
            sinks = [
                s
                for s in self.order
                if (self._channel(s) is not None and self._channel(s).group == group)
                and s not in self.minimised
                and s not in self.hidden
                and s not in self.absent
            ]
            if not sinks:
                continue
            self.body.append(self._build_group(group, sinks))

        self._build_rail()
        if self.rail_dock == "right":
            self.body.append(self.rail)
        else:
            self.root.append(self.rail)
        self.apply_states()
        self._settle_size()

    def _settle_size(self) -> None:
        """Ease the window to the size its contents now want.

        Hiding a strip used to leave the window at its old width with a band
        of empty grey, because GTK only shrinks a window when it is told to.
        The natural size is measured after the rebuild and the window walks
        to it over a few frames, so the change reads as movement.
        """
        _minw, nat_w, _a, _b = self.root.measure(Gtk.Orientation.HORIZONTAL, -1)
        _minh, nat_h, _c, _d = self.root.measure(Gtk.Orientation.VERTICAL, nat_w)
        # No padding term here. ``measure`` already includes the root's
        # margins, so the old ``+14 / +12`` added them a second time and
        # every rebuild left the window a little larger than the size its
        # own contents asked for -- which is why resizing never came back
        # to the same place.
        target = (nat_w, nat_h)
        if self.get_width() <= 1:
            # First layout: nothing to animate from, just be the right size.
            self.set_default_size(*target)
            return
        self._size_target = target
        if self._size_tick is None:
            self._size_tick = GLib.timeout_add(16, self._step_size)

    def _step_size(self) -> bool:
        target = self._size_target
        if target is None:
            self._size_tick = None
            return False
        width, height = self.get_width(), self.get_height()
        dw, dh = target[0] - width, target[1] - height
        if abs(dw) < 2 and abs(dh) < 2:
            self.set_default_size(*target)
            self._size_tick = None
            self._size_target = None
            return False
        # Exponential ease: quick at the start, gentle at the end, and it
        # cannot overshoot however far it has to travel.
        self.set_default_size(
            max(1, int(width + dw * 0.34)),
            max(1, int(height + dh * 0.34)),
        )
        return True

    def _build_group(self, group: str, sinks: list[str]) -> Gtk.Box:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        box.add_css_class("grp")
        # A group frame is a box drawn around the strips it holds, so it has
        # to hug them on both axes. Left to fill, the single-strip mic group
        # stretched into a mostly empty panel in the tall layout.
        box.set_valign(Gtk.Align.START)
        box.set_halign(Gtk.Align.START)
        box.set_hexpand(False)
        box.set_vexpand(False)
        # No group caption: the strips say what they are, and "headset"
        # over a headset group is a label for a label.
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        row.set_valign(Gtk.Align.START)
        for sink in sinks:
            channel = self._channel(sink)
            if channel is None:
                continue
            strip = Strip(self, channel)
            self.strips[sink] = strip
            row.append(strip)
        box.append(row)
        return box

    def _group_tag(self, group: str) -> str:
        if group != "headset":
            return "mic"
        target = self.states.get("vm_game", ChannelState()).device
        return f"headset \u00b7 {target}" if target else "headset"

    def _apply_rail_dock(self) -> None:
        """Point the rail along its docked edge.

        A vertical rail is told to fill the height the strips already ask
        for, never to set it. Before this, each folded channel added a
        148px sliver to a START-aligned column, so two minimised channels
        made the rail taller than the strips and the window grew a band of
        empty grey underneath them.
        """
        vertical = self.rail_dock == "right"
        self.rail.set_orientation(
            Gtk.Orientation.VERTICAL if vertical else Gtk.Orientation.HORIZONTAL
        )
        self.rail.set_valign(Gtk.Align.FILL if vertical else Gtk.Align.START)
        self.rail.set_halign(Gtk.Align.START if vertical else Gtk.Align.FILL)
        self.rail_handle.set_label("\u2193" if vertical else "\u2192")
        self.rail_handle.set_tooltip_text(
            "Stack minimised channels along the bottom"
            if vertical
            else "Stack minimised channels down the right"
        )

    def _on_rail_dock(self, _button: Gtk.Button) -> None:
        self.rail_dock = "bottom" if self.rail_dock == "right" else "right"
        self._apply_rail_dock()
        self.rebuild()
        self._save()

    def _build_rail(self) -> None:
        child = self.rail.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            if child is not self.rail_handle:
                self.rail.remove(child)
            child = nxt
        vertical = self.rail_dock == "right"
        for sink in self.order:
            if sink in self.absent:
                # No rail tag either: an unplugged device should leave
                # no trace to click, or the click would do nothing.
                continue
            if sink not in self.minimised and sink not in self.hidden:
                continue
            channel = self._channel(sink)
            if channel is None:
                continue
            # A readable floor for the name, then expand into whatever the
            # strips leave. The sliver shares the strip height instead of
            # dictating a taller window.
            sliver = Sliver(
                self.pal,
                channel.name,
                56 if vertical else 84,
                horizontal=not vertical,
            )
            sliver.set_vexpand(vertical)
            sliver.set_hexpand(not vertical)
            click = Gtk.GestureClick()
            click.connect("released", lambda *_a, s=sink: self.restore(s))
            sliver.add_css_class("sliver")
            sliver.add_controller(click)
            sliver.set_tooltip_text(f"Restore {channel.name}")
            self.rail.append(sliver)
            self.slivers[sink] = sliver
        self.rail.set_visible(bool(self.minimised or self.hidden))

    # -- user actions ------------------------------------------------------
    def minimise(self, sink: str) -> None:
        self.minimised.add(sink)
        self.rebuild()
        self._save()

    def restore(self, sink: str) -> None:
        self.minimised.discard(sink)
        self.hidden.discard(sink)
        self.rebuild()
        self._save()

    def unhide(self, sink: str) -> None:
        self.hidden.discard(sink)
        self.rebuild()
        self._save()

    def reorder(self, moved: str, target: str) -> None:
        if moved == target or moved not in self.order or target not in self.order:
            return
        # Always inserting before the target meant a strip could only
        # ever travel leftwards; dropping it to the right of where it
        # started put it back exactly where it was.
        forward = self.order.index(moved) < self.order.index(target)
        self.order.remove(moved)
        at = self.order.index(target)
        self.order.insert(at + 1 if forward else at, moved)
        self.rebuild()
        self._save()

    def _save(self) -> None:
        values = dict(self.cfg)
        width, height = self.get_default_size()
        values.update(
            {
                "mixer_hidden": sorted(self.hidden),
                "mixer_minimised": sorted(self.minimised),
                "mixer_order": list(self.order),
                "mixer_rail_dock": self.rail_dock,
                "win_w": int(width or values.get("win_w", 880)),
                "win_h": int(height or values.get("win_h", 620)),
            }
        )
        self.cfg = values
        try:
            settings_mod.save(values)
        except OSError:
            pass  # a read-only config must not take the mixer down

    # -- live data ---------------------------------------------------------
    def _start_backends(self) -> None:
        # Both of these are daemon threads: constructing one does nothing until
        # it is started, and neither is stopped for us at interpreter exit.
        self.collector = collector_mod.Collector((), self._on_snapshot, interval=0.5)
        self.collector.set_window_visible(True)
        self.collector.start()
        self.collector.refresh_now()
        sources = [f"{c.sink}.monitor" for c in CHANNELS]
        self.peaks = meters_mod.PeakPoller(sources=sources)
        self.peaks.start()
        self._frame_ms = FRAME_IDLE_MS
        self._frame_id = GLib.timeout_add(self._frame_ms, self._on_frame)

    def _on_snapshot(self, snapshot) -> None:
        GLib.idle_add(self._apply_snapshot, snapshot)

    def _read_routes(self) -> dict[str, set[str]]:
        """source -> sinks, straight from the live module table."""
        routes: dict[str, set[str]] = {}
        try:
            for loop in pactl.select_loopbacks(pactl.modules()):
                routes.setdefault(loop.source, set()).add(loop.sink)
        except OSError:  # pragma: no cover - no pactl means no routing
            return {}
        return routes

    def _read_muted_outs(self) -> set[str]:
        """Friendly names of destination devices that are muted themselves.

        The LPD8 script mutes hardware endpoints directly, so the mixer can
        show every bus open and the room can still be silent. This is the
        one place that discrepancy becomes visible.
        """
        muted: set[str] = set()
        wanted = {sink for _label, sink in OUTPUTS}
        reachable = {entry.name for entry in pactl.sinks()} if wanted else set()
        for label, sink in OUTPUTS:
            if sink in reachable and pactl.is_muted(sink):
                muted.add(label)
        return muted

    def _apply_snapshot(self, snapshot) -> bool:
        self._routes = self._read_routes()
        try:
            self._muted_outs = self._read_muted_outs()
        except OSError:  # pragma: no cover - pactl gone
            self._muted_outs = set()
        # Snapshot keeps both of these as dicts keyed by sink name.
        sinks = dict(snapshot.sinks)
        streams: dict[str, list[Listener]] = {
            sink: [
                Listener(name=s.name or "app", muted=bool(s.muted), index=s.index) for s in entries
            ]
            for sink, entries in snapshot.streams.items()
        }
        for channel in CHANNELS:
            sink = sinks.get(channel.sink)
            state = self.states[channel.sink]
            previous, was_seen = state.volume, state.seen
            state.present = sink is not None
            state.volume = int(sink.volume) if sink else 0
            state.seen = True
            strip = self.strips.get(channel.sink)
            if (
                was_seen
                and state.present
                and strip is not None
                and state.volume != previous
                and not strip.held
            ):
                strip.mark_hardware()
            was_muted, first = state.muted, not was_seen
            state.muted = bool(sink.muted) if sink else False
            if state.present and (first or state.muted != was_muted):
                # Done here rather than in the mute handler so a mute from
                # anywhere -- this window, a pad, pavucontrol, a script --
                # reaches the pad LED. The surface has no screen; a lit pad
                # over a silent bus is the whole problem.
                self._push_led(channel.sink, state.muted)
            state.listeners = streams.get(channel.sink, [])
            state.device = self._device_label(channel, snapshot)
        if self._track_absent():
            self.rebuild()
        self.apply_states()
        self._update_banner()
        self._feed_remap()
        return False

    def _push_led(self, sink: str, muted: bool) -> None:
        """Mirror one bus's mute state onto its LPD8 pad.

        The port is looked up once and re-looked-up only after a failed
        write, so an unplugged controller costs one failed ``amidi`` call
        rather than one per snapshot.
        """
        if leds.pad_for_sink(sink) is None:
            return
        if self._led_port is None:
            self._led_port = leds.find_port()
            if self._led_port is None:
                return
        channel = int(
            self.cfg.get("led_midi_channel", leds.DEFAULT_CHANNEL) or leds.DEFAULT_CHANNEL
        )
        try:
            sent = leds.set_sink_mute(self._led_port, sink, muted, channel)
        except OSError:  # pragma: no cover - the surface is not load-bearing
            sent = False
        if not sent:
            self._led_port = None

    def _device_label(self, channel: Channel, snapshot) -> str:
        """Where this bus lands. Short, real, and never a routing path."""
        if channel.group == "mic":
            # The mic is never raw here: the 2i2 goes through Carla's
            # chain first, so what this bus carries is the processed
            # signal, and the readout has to say so.
            return "2i2 \u2192 Carla \u2192 apps"
        routed = self._routed_labels(channel.sink)
        if routed:
            # Name the endpoints this bus is actually wired to. The old
            # readout picked whichever A50 endpoint was RUNNING, and both
            # always are, so it said "A50 game" while the user listened on
            # chat.
            muted = [name for name in routed if name in self._muted_outs]
            if muted:
                # A muted endpoint is silence with no explanation anywhere
                # on screen, which is exactly how the A50 went quiet.
                return " + ".join(routed) + "  \u2014 MUTED"
            return " + ".join(routed)
        if channel.sink == "laptop_audio":
            return "laptop in"
        return "not routed" if self.states[channel.sink].present else "\u2014"

    def _routed_labels(self, sink: str) -> list[str]:
        """Friendly names of the sinks this bus loops back into."""
        dests = self._routes.get(f"{sink}.monitor", set())
        names = [label for label, target in OUTPUTS if target in dests]
        known = {target for _label, target in OUTPUTS}
        # A destination we have no name for is still a destination.
        names += [d.split(".")[-1] for d in sorted(dests) if d not in known]
        return names

    def _headset_target(self, snapshot) -> str:
        """Which A50 endpoint is live. The pair flips between game and chat.

        A running endpoint wins; otherwise the headset is present but idle,
        which is worth saying plainly rather than guessing a side.
        """
        seen = False
        for name, sink in snapshot.sinks.items():
            if "Astro_A50" not in name:
                continue
            seen = True
            if str(getattr(sink, "state", "")).upper().startswith("RUN"):
                return "A50 chat" if name.endswith("chat") else "A50 game"
        return "A50 idle" if seen else "no headset"

    def apply_states(self) -> None:
        for sink, strip in self.strips.items():
            strip.apply(self.states[sink])

    def _track_absent(self) -> bool:
        """Fold vanished buses away, unfold returning ones. True if changed.

        A bus is only restored to the strip area if the *device* came
        back; a bus the user hid or minimised by hand stays where they
        put it, because the mixer must not undo a deliberate choice.
        """
        changed = False
        for channel in CHANNELS:
            sink = channel.sink
            present = self.states[sink].present
            if present:
                self.ever_present.add(sink)
                if sink in self.absent:
                    self.absent.discard(sink)
                    changed = True
            elif sink not in self.absent:
                self.absent.add(sink)
                changed = True
        return changed

    def _update_banner(self) -> None:
        # Absence is handled by folding the strip away. The banner is
        # kept for the case that really is a fault: a bus that was up
        # this session and then died under us.
        missing = [
            c
            for c in CHANNELS
            if not self.states[c.sink].present
            and c.sink not in self.hidden
            and c.sink in self.ever_present
        ]
        if missing:
            names = ", ".join(c.name for c in missing)
            self.banner_text.set_text(f"{names} missing - the audio graph is not fully up")
            units: list[str] = []
            for channel in missing:
                unit = UNIT_FOR_SINK.get(channel.sink, "")
                if unit and unit not in units:
                    units.append(unit)
            self.banner_units = tuple(units)
            self.banner_button.set_visible(bool(units))
            self.banner_button.set_tooltip_text("Restart " + ", ".join(units) if units else "")
        else:
            # The banner hides, but the button underneath stayed armed with
            # the units from the last fault. Disarm it when the graph is well.
            self.banner_units = ()
            self.banner_button.set_visible(False)
            self.banner_button.set_sensitive(True)
        self.banner.set_visible(bool(missing))

    def _on_restart(self, _button: Gtk.Button) -> None:
        """Restart whichever units own the buses that are missing."""
        if not self.banner_units:
            return
        services_mod.restart(*self.banner_units)
        self.banner_text.set_text(
            "restarting "
            + ", ".join(
                u.removeprefix("roaring-").removesuffix(".service") for u in self.banner_units
            )
        )
        # systemd is asynchronous; re-read rather than guess the outcome.
        self.banner_button.set_sensitive(False)
        GLib.timeout_add_seconds(4, self._restart_settled)

    def _restart_settled(self) -> bool:
        self.banner_button.set_sensitive(True)
        self.collector.refresh_now()
        return False

    def _on_frame(self) -> bool:
        available = getattr(self.peaks, "available", True)
        for sink, strip in self.strips.items():
            level = self.peaks.level(f"{sink}.monitor") if available else 0.0
            strip.meter.feed(level)
            strip.meter.tick()
            strip.tick_hardware()
        for sink, sliver in self.slivers.items():
            level = self.peaks.level(f"{sink}.monitor") if available else 0.0
            sliver.feed(level, self.states[sink].muted)
        self._tick_laptop_idle()
        wanted = FRAME_BUSY_MS if time.monotonic() <= self._busy_until else FRAME_IDLE_MS
        if wanted != self._frame_ms:
            # The rate is changed by replacing the source, so the fast
            # clock exists only for the second it is wanted and the idle
            # window is never paying for frames it does not use.
            self._frame_ms = wanted
            self._frame_id = GLib.timeout_add(wanted, self._on_frame)
            return False
        return True

    def quicken(self) -> None:
        """Redraw fast for a second: something is being dragged.

        Called by the controls themselves rather than guessed at from a
        timer, so the expensive rate is on exactly while a hand is on the
        window and off the moment it lets go.
        """
        self._busy_until = time.monotonic() + BUSY_S
        if self._frame_ms == FRAME_BUSY_MS or not self._frame_id:
            return
        GLib.source_remove(self._frame_id)
        self._frame_ms = FRAME_BUSY_MS
        self._frame_id = GLib.timeout_add(FRAME_BUSY_MS, self._on_frame)

    def _feed_remap(self) -> None:
        """Give the LPD8 window the state this snapshot already carries.

        It draws knob positions and lit pads from this, so it polls
        nothing of its own; closed, it costs one attribute lookup.
        """
        window = self._remap_window
        if window is None or not window.get_mapped():
            return
        feed = getattr(window, "apply_live", None)
        if feed is not None:
            feed(self.states)

    def _tick_laptop_idle(self) -> None:
        """Fold the laptop strip away once it has been quiet long enough.

        Only automatic in one direction: it collapses itself, and the
        rail restores it, so the mixer never reopens a strip under the
        user's hand.
        """
        sink = "laptop_audio"
        state = self.states.get(sink)
        if state is None:
            return
        level = self.peaks.level(f"{sink}.monitor") if self.peaks else 0.0
        if state.listeners or level > 0.02 or not state.present:
            self._laptop_busy = time.monotonic()
            return
        if sink in self.minimised or sink in self.hidden or sink in self.absent:
            return
        if time.monotonic() - self._laptop_busy >= IDLE_COLLAPSE_S:
            self.minimise(sink)

    # -- setup -----------------------------------------------------------
    def open_setup(self) -> None:
        """Build the Setup window the first time it is wanted, then show it.

        The import is deliberately here and not at the top of the module:
        ``hearth.ui.gtk4.setup`` pulls in the unit scanner and the legacy
        conf reader, and a mixer that never opens Setup should never pay
        for either.
        """
        if self._setup_window is None:
            from hearth.ui.gtk4.setup import SetupWindow

            self._setup_window = SetupWindow(self)
        self._setup_window.present()

    def open_remap(self) -> None:
        """Build the LPD8 window the first time it is wanted, then show it.

        Same deal as Setup: the import is here so a mixer that never opens
        the controller window never pays for it, and the window is kept
        rather than rebuilt so reopening it is free.
        """
        if self._remap_window is None:
            from hearth.ui.gtk4.remap import RemapWindow

            self._remap_window = RemapWindow(self)
        self._remap_window.present()

    def _on_key(self, _controller, keyval: int, _code: int, state) -> bool:
        if keyval == Gdk.KEY_comma and state & Gdk.ModifierType.CONTROL_MASK:
            self.open_setup()
            return True
        if keyval == Gdk.KEY_l and state & Gdk.ModifierType.CONTROL_MASK:
            self.open_remap()
            return True
        return False

    def _on_right_click(self, _gesture, _n_press: int, x: float, y: float) -> None:
        if self._menu is None:
            items = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            for label, handler in (
                ("Setup\u2026", self._on_menu_setup),
                ("LPD8 controls\u2026", self._on_menu_remap),
            ):
                item = Gtk.Button(label=label)
                item.add_css_class("rail-handle")
                item.connect("clicked", handler)
                items.append(item)
            self._menu = Gtk.Popover()
            self._menu.set_child(items)
            self._menu.set_has_arrow(False)
            self._menu.set_parent(self)
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
        self._menu.set_pointing_to(rect)
        self._menu.popup()

    def _on_menu_setup(self, _button: Gtk.Button) -> None:
        if self._menu is not None:
            self._menu.popdown()
        self.open_setup()

    def _on_menu_remap(self, _button: Gtk.Button) -> None:
        if self._menu is not None:
            self._menu.popdown()
        self.open_remap()

    def _on_close(self, *_args) -> bool:
        self._save()
        self.shutdown()
        return False

    def shutdown(self) -> None:
        """Stop the pollers. ``parec`` leaks if the peak poller is not stopped."""
        setup_window = getattr(self, "_setup_window", None)
        if setup_window is not None:
            # Closing it only hides it, which stops the poll; this is the
            # teardown that also gives the widgets back.
            setup_window.destroy()
            self._setup_window = None
        remap_window = getattr(self, "_remap_window", None)
        if remap_window is not None:
            remap_window.destroy()
            self._remap_window = None
        if getattr(self, "_frame_id", None):
            GLib.source_remove(self._frame_id)
            self._frame_id = 0
        for poller in (getattr(self, "peaks", None), getattr(self, "collector", None)):
            try:
                if poller is not None:
                    poller.stop()
            except Exception:  # pragma: no cover - shutdown must not raise
                pass


class MixerApp(Gtk.Application):
    def __init__(self) -> None:
        super().__init__(application_id=APP_ID)
        self.pal = style_mod.palette()
        self.window: MixerWindow | None = None
        self._ipc: ipc_mod.Server | None = None

    def do_startup(self) -> None:
        Gtk.Application.do_startup(self)
        provider = Gtk.CssProvider()
        load_css(provider, style_mod.CSS(self.pal))
        display = Gdk.Display.get_default()
        if display is not None:
            Gtk.StyleContext.add_provider_for_display(
                display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )
        self._start_control_socket()

    def do_activate(self) -> None:
        if self.window is None:
            self.window = MixerWindow(self)
        self.window.present()

    def do_shutdown(self) -> None:
        if self._ipc is not None:
            self._ipc.stop()
            self._ipc = None
        if self.window is not None:
            self.window.shutdown()
        Gtk.Application.do_shutdown(self)

    # Control socket
    #
    # The GTK3 window used to own this. Padfire polls ``get_sinks`` every
    # three seconds, the desktop entry sends ``unmute``, and ``hearth
    # --quit`` / ``--show`` are the only way to drive a window that lives
    # in the tray of no tray, so the socket had to move here rather than
    # die with the old app.

    def _start_control_socket(self) -> None:
        ipc_mod.write_pid()
        server = ipc_mod.Server(self._on_control)
        try:
            server.start()
        except OSError as exc:
            # A mixer that works is worth more than a mixer with IPC, and
            # a second instance losing the bind must not be fatal.
            log.warning("control socket unavailable: %s", exc)
            return
        self._ipc = server

    def _on_control(self, request: str) -> str:
        """Answer one socket request. Runs on the IPC thread, never the main one."""
        return control.dispatch(
            request,
            {
                "show": self._ctl_show,
                "quit": self._ctl_quit,
                "unmute": self._ctl_unmute,
                "get_sinks": self._ctl_sinks,
                "dump": self._ctl_dump,
                "remap": self._ctl_remap,
                "padfire_status": lambda: "ok",
            },
        )

    def _ctl_show(self) -> str:
        GLib.idle_add(self.activate)
        return "ok"

    def _ctl_quit(self) -> str:
        GLib.idle_add(self.quit)
        return "quitting"

    def _ctl_unmute(self) -> str:
        def work() -> None:
            for channel in CHANNELS:
                try:
                    pactl.set_mute(channel.sink, False)
                except Exception as exc:  # a missing bus must not stop the rest
                    log.warning("unmute %s: %s", channel.sink, exc)

        threading.Thread(target=work, daemon=True, name="hearth-unmute").start()
        return "ok"

    def _ctl_sinks(self) -> str:
        """Report what the window already knows, not what pactl would say.

        The old answer shelled out twice per bus on every poll. The window
        refreshes this state twice a second regardless, so the socket reads
        it and costs nothing.
        """
        window = self.window
        if window is None:
            return control.sinks_payload(())
        return control.sinks_payload(
            (c.sink, c.name, window.states[c.sink].volume, window.states[c.sink].muted)
            for c in CHANNELS
        )

    def _ctl_remap(self) -> str:
        """Open the LPD8 window, starting the mixer's own window if needed."""

        def show() -> bool:
            self.activate()
            if self.window is not None:
                self.window.open_remap()
            return False

        GLib.idle_add(show)
        return "ok"

    def _ctl_dump(self) -> str:
        from hearth import paths

        target = paths.dumps_dir() / "summary.txt"

        def work() -> None:
            from hearth import status

            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(status.text() + "\n")
            except Exception as exc:  # pragma: no cover - diagnostic only
                log.warning("dump failed: %s", exc)

        threading.Thread(target=work, daemon=True, name="hearth-dump").start()
        return f"dumping -> {target}"


def main(argv: list[str] | None = None) -> int:
    """Run the GTK4 mixer.

    When the user unit starts before the graphical session is ready, GTK
    raises "Gtk couldn't be initialized" from deep inside the window
    constructor and the mixer stays dead for the rest of the login. Check
    for a display first and exit with a plain message instead, so the
    unit's Restart=on-failure can simply try again a moment later.
    """
    if not Gtk.init_check() or Gdk.Display.get_default() is None:
        print(
            "hearth: no display yet (DISPLAY/WAYLAND_DISPLAY unset or refused);"
            " waiting for the graphical session",
            file=sys.stderr,
        )
        return 1
    return MixerApp().run(argv if argv is not None else [])


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
