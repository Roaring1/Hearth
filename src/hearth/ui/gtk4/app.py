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

import sys
import time
from dataclasses import dataclass, field

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")

from gi.repository import Gdk, GLib, Gtk  # noqa: E402 - must follow require_version

from hearth import collector as collector_mod  # noqa: E402
from hearth import meters as meters_mod  # noqa: E402
from hearth import settings as settings_mod  # noqa: E402
from hearth.audio import pactl  # noqa: E402
from hearth.ui.gtk4 import style as style_mod  # noqa: E402
from hearth.ui.gtk4.widgets import Fader, Meter, Scale, Sliver, parse_rgb, rounded  # noqa: E402

APP_ID = "co.roaring.Hearth"

#: Meter height, expanded and compact. Everything else in a strip is fixed, so
#: this single number is what "show apps" actually changes.
METER_TALL = 104
METER_SHORT = 76

#: How long a fader stays "held" after the user touches it, so an in-flight
#: snapshot cannot yank the cap back under their finger.
HOLD_S = 1.2


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
    Channel("Stream", "mic_b1", "mic", "K8", "P6"),
    Channel("Discord", "mic_b2", "mic"),
)

SHARE_SINK = "vm_share"

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


@dataclass
class Listener:
    """One application playing into a bus."""

    name: str
    muted: bool = False
    live: bool = True


@dataclass
class ChannelState:
    """What the window knows about a channel right now."""

    volume: int = 0
    muted: bool = False
    present: bool = False
    device: str = ""
    listeners: list[Listener] = field(default_factory=list)


class Strip(Gtk.Box):
    """One channel: header, app marks, meter and fader, value, mute, device."""

    def __init__(self, window: MixerWindow, channel: Channel) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        self.win = window
        self.channel = channel
        self.pal = window.pal
        self.add_css_class("strip")
        self.set_size_request(104, -1)
        self.set_valign(Gtk.Align.START)
        self._held_until = 0.0

        # header: grip, name, minimise
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        header.append(self._grip())
        name = Gtk.Label(label=channel.name.upper(), xalign=0.0)
        name.add_css_class("strip-name")
        name.set_hexpand(True)
        header.append(name)
        minimise = Gtk.Button(label="\u2013")
        minimise.add_css_class("minbox")
        minimise.set_tooltip_text(f"Minimise {channel.name} to the rail")
        minimise.connect("clicked", lambda _b: window.minimise(channel.sink))
        header.append(minimise)
        self.append(header)

        # app marks
        self.icons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=3)
        self.icons.set_size_request(-1, 14)
        self.append(self.icons)

        # meter + fader
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        row.set_halign(Gtk.Align.CENTER)
        self.meter = Meter(self.pal, height=window.meter_h)
        self.fader = Fader(self.pal, height=window.meter_h)
        self.fader.connect("moved", self._on_moved)
        row.append(self.meter)
        row.append(self.fader)
        self.append(row)

        # value + bind chip
        vrow = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self.value = Gtk.Label(label="--", xalign=0.0)
        self.value.add_css_class("value")
        self.value.set_hexpand(True)
        vrow.append(self.value)
        if channel.bind:
            chip = Gtk.Label(label=channel.bind)
            chip.add_css_class("bind")
            chip.set_valign(Gtk.Align.CENTER)
            chip.set_tooltip_text(self._bind_tip())
            vrow.append(chip)
        self.append(vrow)

        # mute + send
        brow = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self.mute = Gtk.ToggleButton(label="MUTE")
        self.mute.add_css_class("mute")
        self.mute.set_hexpand(True)
        self._mute_handler = self.mute.connect("toggled", self._on_mute)
        brow.append(self.mute)
        self.send = Gtk.Button(label="\u2197")
        self.send.add_css_class("sendbtn")
        self.send.set_tooltip_text(f"{channel.name} is sent to the share bus")
        self.send.set_sensitive(False)
        brow.append(self.send)
        self.append(brow)

        # device line
        self.device = Gtk.Label(label="\u2026", xalign=0.0)
        self.device.add_css_class("device")
        self.device.set_ellipsize(3)  # PANGO_ELLIPSIZE_END
        self.append(self.device)

        # listeners, expanded only
        self.listeners = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.append(self.listeners)
        self.listeners.set_visible(window.expanded)

        self._add_drag()

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
        return f"LPD8 knob {self.channel.bind}{pad}"

    def _add_drag(self) -> None:
        source = Gtk.DragSource()
        source.set_actions(Gdk.DragAction.MOVE)
        source.connect(
            "prepare",
            lambda *_a: Gdk.ContentProvider.new_for_value(self.channel.sink),
        )
        self.add_controller(source)
        target = Gtk.DropTarget.new(str, Gdk.DragAction.MOVE)
        target.connect("drop", self._on_drop)
        self.add_controller(target)

    def _on_drop(self, _t: Gtk.DropTarget, value: str, _x: float, _y: float) -> bool:
        self.win.reorder(str(value), self.channel.sink)
        return True

    # -- user input ------------------------------------------------------
    def _on_moved(self, _f: Fader, value: int) -> None:
        self._held_until = time.monotonic() + HOLD_S
        self.value.set_text(f"{value}")
        pactl.set_volume(self.channel.sink, value)

    def _on_mute(self, button: Gtk.ToggleButton) -> None:
        active = button.get_active()
        button.set_label("MUTED" if active else "MUTE")
        pactl.set_mute(self.channel.sink, active)

    # -- state -----------------------------------------------------------
    def set_meter_height(self, height: int) -> None:
        self.meter.set_content_height(height)
        self.fader.set_content_height(height)

    def set_expanded(self, expanded: bool) -> None:
        self.listeners.set_visible(expanded)

    def apply(self, state: ChannelState) -> None:
        dead = not state.present
        self.set_css_state(dead)
        self.meter.set_state(muted=state.muted, dead=dead)
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

        self._fill_icons(state.listeners)
        self._fill_listeners(state.listeners)

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

    def _fill_icons(self, listeners: list[Listener]) -> None:
        self._clear(self.icons)
        for listener in listeners[:4]:
            mark = Gtk.Label(label=listener.name[:1].upper() or "?")
            mark.add_css_class("mark")
            mark.set_tooltip_text(listener.name)
            brand = app_class(listener.name)
            if brand:
                mark.add_css_class(brand)
            self.icons.append(mark)
        extra = len(listeners) - 4
        if extra > 0:
            more = Gtk.Label(label=f"+{extra}")
            more.add_css_class("mark")
            more.add_css_class("more")
            more.set_tooltip_text(", ".join(x.name for x in listeners[4:]))
            self.icons.append(more)

    def _fill_listeners(self, listeners: list[Listener]) -> None:
        self._clear(self.listeners)
        for listener in listeners[:5]:
            row = Gtk.Label(label=listener.name, xalign=0.0)
            row.add_css_class("listener")
            row.set_ellipsize(3)
            if listener.muted:
                row.add_css_class("muted")
            self.listeners.append(row)
        if not listeners:
            empty = Gtk.Label(label="nothing playing", xalign=0.0)
            empty.add_css_class("listener")
            self.listeners.append(empty)


class Ghost(Gtk.Box):
    """A hidden channel, shown in place as a dashed outline with SHOW."""

    def __init__(self, window: MixerWindow, channel: Channel) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.add_css_class("strip")
        self.add_css_class("ghost")
        self.set_size_request(104, window.meter_h + 96)
        self.set_valign(Gtk.Align.START)
        name = Gtk.Label(label=channel.name.upper())
        name.add_css_class("strip-name")
        name.set_valign(Gtk.Align.CENTER)
        name.set_vexpand(True)
        self.append(name)
        show = Gtk.Button(label="SHOW")
        show.add_css_class("showbtn")
        show.connect("clicked", lambda _b: window.unhide(channel.sink))
        self.append(show)


class ShareTile(Gtk.Box):
    """The share destination: what the stream is actually hearing."""

    def __init__(self, window: MixerWindow) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.win = window
        self.pal = window.pal
        self.add_css_class("dest")
        self.set_size_request(150, -1)
        self.set_valign(Gtk.Align.START)
        self._level = 0.0

        tag = Gtk.Label(label="TO STREAM", xalign=0.0)
        tag.add_css_class("dest-tag")
        self.append(tag)
        name = Gtk.Label(label="SHARE", xalign=0.0)
        name.add_css_class("strip-name")
        self.append(name)

        self.bar = Gtk.DrawingArea()
        self.bar.set_content_height(12)
        self.bar.set_draw_func(self._draw_bar)
        self.append(self.bar)

        self.warn = Gtk.Label(label="", xalign=0.0)
        self.warn.add_css_class("dest-warn")
        self.warn.set_visible(False)
        self.append(self.warn)

        self.feeders = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.append(self.feeders)

    def _draw_bar(self, _a, cr, w: int, h: int) -> None:
        cr.set_source_rgb(*parse_rgb(self.pal["meter-off"]))
        rounded(cr, 0, 0, w, h, 3)
        cr.fill()
        if self._level <= 0.005:
            return
        colour = "bad" if self._level > 0.9 else ("hot" if self._level > 0.74 else "ok")
        cr.set_source_rgb(*parse_rgb(self.pal[colour]))
        rounded(cr, 0, 0, max(3.0, w * self._level), h, 3)
        cr.fill()

    def feed(self, level: float) -> None:
        self._level = max(0.0, min(1.0, level))
        self.bar.queue_draw()

    def apply(self, feeders: list[Listener], present: bool) -> None:
        child = self.feeders.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.feeders.remove(child)
            child = nxt
        for feeder in feeders[:6]:
            row = Gtk.Label(label=feeder.name, xalign=0.0)
            row.add_css_class("listener")
            row.set_ellipsize(3)
            self.feeders.append(row)
        bad = present and not feeders
        self.warn.set_visible(bad or not present)
        if not present:
            self.warn.set_text("share bus is missing")
        elif bad:
            self.warn.set_text("nothing is feeding it")
        if bad or not present:
            self.add_css_class("bad")
        else:
            self.remove_css_class("bad")


class MixerWindow(Gtk.ApplicationWindow):
    """The whole window. Groups on the left, share, then the minimise rail."""

    def __init__(self, app: Gtk.Application) -> None:
        super().__init__(application=app, title="Hearth")
        self.add_css_class("hearth")
        self.pal = app.pal
        self.cfg = settings_mod.load()
        self.expanded = not bool(self.cfg.get("mixer_collapsed", False))
        self.meter_h = METER_TALL if self.expanded else METER_SHORT
        self.hidden = set(self.cfg.get("mixer_hidden") or ["mic_b1"])
        self.minimised = set(self.cfg.get("mixer_minimised") or [])
        self.order = self._load_order()
        self.states: dict[str, ChannelState] = {c.sink: ChannelState() for c in CHANNELS}
        self.share_state = ChannelState()
        self.share_feeders: list[Listener] = []
        self.strips: dict[str, Strip] = {}
        self.slivers: dict[str, Sliver] = {}
        self.group_tags: dict[str, Gtk.Label] = {}

        # Width is remembered; height follows the content. A mixer with a
        # fixed strip height has one correct height, and restoring a taller
        # one just leaves a slab of empty window under the strips.
        self.set_default_size(int(self.cfg.get("win_w", 880) or 880), -1)
        self.set_resizable(True)

        self.root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.root.set_margin_top(10)
        self.root.set_margin_bottom(8)
        self.root.set_margin_start(10)
        self.root.set_margin_end(10)
        self.set_child(self.root)

        self.banner = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.banner.add_css_class("banner")
        self.banner_text = Gtk.Label(label="", xalign=0.0)
        self.banner_text.add_css_class("banner-text")
        self.banner.append(self.banner_text)
        self.banner.set_visible(False)
        self.root.append(self.banner)

        self.body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.body.set_vexpand(True)
        self.root.append(self.body)

        self.share = ShareTile(self)
        self.rail = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.rail.set_valign(Gtk.Align.START)
        rail_tag = Gtk.Label(label="MIN")
        rail_tag.add_css_class("rail-tag")
        self.rail.append(rail_tag)

        self.footer = self._build_footer()
        self.root.append(self.footer)

        self.rebuild()
        self._start_backends()
        self.connect("close-request", self._on_close)

    # -- layout ----------------------------------------------------------
    def _load_order(self) -> list[str]:
        saved = [s for s in (self.cfg.get("mixer_order") or []) if isinstance(s, str)]
        known = [c.sink for c in CHANNELS]
        ordered = [s for s in saved if s in known]
        ordered += [s for s in known if s not in ordered]
        return ordered

    def _build_footer(self) -> Gtk.Box:
        footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        footer.add_css_class("footer")
        self.hidden_label = Gtk.Label(label="", xalign=0.0)
        self.hidden_label.add_css_class("footer")
        self.hidden_label.set_hexpand(True)
        footer.append(self.hidden_label)
        self.drawer = Gtk.Button(label="show apps \u25be")
        self.drawer.add_css_class("drawer")
        self.drawer.connect("clicked", self._toggle_expanded)
        footer.append(self.drawer)
        spacer = Gtk.Label(label="")
        spacer.set_hexpand(True)
        footer.append(spacer)
        return footer

    def _channel(self, sink: str) -> Channel | None:
        for channel in CHANNELS:
            if channel.sink == sink:
                return channel
        return None

    def rebuild(self) -> None:
        """Rebuild the strip area from order, hidden and minimised state."""
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
            ]
            if not sinks:
                continue
            self.body.append(self._build_group(group, sinks))

        self.body.append(self.share)
        self._build_rail()
        self.body.append(self.rail)
        self._refresh_footer()
        self.apply_states()

    def _build_group(self, group: str, sinks: list[str]) -> Gtk.Box:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        box.add_css_class("grp")
        box.set_valign(Gtk.Align.START)
        tag = Gtk.Label(label=self._group_tag(group), xalign=0.0)
        tag.add_css_class("grp-tag")
        self.group_tags[group] = tag
        box.append(tag)
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        for sink in sinks:
            channel = self._channel(sink)
            if channel is None:
                continue
            if sink in self.hidden:
                row.append(Ghost(self, channel))
                continue
            strip = Strip(self, channel)
            self.strips[sink] = strip
            row.append(strip)
        scale = Scale(self.pal, self.meter_h)
        scale.set_valign(Gtk.Align.START)
        scale.set_margin_top(54)
        row.append(scale)
        box.append(row)
        return box

    def _group_tag(self, group: str) -> str:
        if group != "headset":
            return "mic"
        target = self.states.get("vm_game", ChannelState()).device
        return f"headset \u00b7 {target}" if target else "headset"

    def _build_rail(self) -> None:
        child = self.rail.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            if not isinstance(child, Gtk.Label):
                self.rail.remove(child)
            child = nxt
        for sink in self.order:
            if sink not in self.minimised:
                continue
            channel = self._channel(sink)
            if channel is None:
                continue
            sliver = Sliver(self.pal, channel.name, self.meter_h + 60)
            click = Gtk.GestureClick()
            click.connect("released", lambda *_a, s=sink: self.restore(s))
            sliver.add_controller(click)
            sliver.set_tooltip_text(f"Restore {channel.name}")
            self.rail.append(sliver)
            self.slivers[sink] = sliver
        self.rail.set_visible(bool(self.minimised))

    def _refresh_footer(self) -> None:
        count = len(self.hidden)
        self.hidden_label.set_text(f"{count} hidden" if count else "")
        self.drawer.set_label("hide apps \u25b4" if self.expanded else "show apps \u25be")

    # -- user actions ------------------------------------------------------
    def _toggle_expanded(self, _button: Gtk.Button) -> None:
        self.expanded = not self.expanded
        self.meter_h = METER_TALL if self.expanded else METER_SHORT
        self.rebuild()
        self._save()

    def minimise(self, sink: str) -> None:
        self.minimised.add(sink)
        self.rebuild()
        self._save()

    def restore(self, sink: str) -> None:
        self.minimised.discard(sink)
        self.rebuild()
        self._save()

    def unhide(self, sink: str) -> None:
        self.hidden.discard(sink)
        self.rebuild()
        self._save()

    def reorder(self, moved: str, target: str) -> None:
        if moved == target or moved not in self.order or target not in self.order:
            return
        self.order.remove(moved)
        self.order.insert(self.order.index(target), moved)
        self.rebuild()
        self._save()

    def _save(self) -> None:
        values = dict(self.cfg)
        width, height = self.get_default_size()
        values.update(
            {
                "mixer_collapsed": not self.expanded,
                "mixer_hidden": sorted(self.hidden),
                "mixer_minimised": sorted(self.minimised),
                "mixer_order": list(self.order),
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
        sources = [f"{c.sink}.monitor" for c in CHANNELS] + [f"{SHARE_SINK}.monitor"]
        self.peaks = meters_mod.PeakPoller(sources=sources)
        self.peaks.start()
        self._frame_id = GLib.timeout_add(66, self._on_frame)

    def _on_snapshot(self, snapshot) -> None:
        GLib.idle_add(self._apply_snapshot, snapshot)

    def _apply_snapshot(self, snapshot) -> bool:
        # Snapshot keeps both of these as dicts keyed by sink name.
        sinks = dict(snapshot.sinks)
        streams: dict[str, list[Listener]] = {
            sink: [Listener(name=s.name or "app", muted=bool(s.muted)) for s in entries]
            for sink, entries in snapshot.streams.items()
        }
        for channel in CHANNELS:
            sink = sinks.get(channel.sink)
            state = self.states[channel.sink]
            state.present = sink is not None
            state.volume = int(sink.volume) if sink else 0
            state.muted = bool(sink.muted) if sink else False
            state.listeners = streams.get(channel.sink, [])
            state.device = self._device_label(channel, snapshot)
        share = sinks.get(SHARE_SINK)
        self.share_state.present = share is not None
        self.share_feeders = streams.get(SHARE_SINK, [])
        self.apply_states()
        self._update_banner()
        return False

    def _device_label(self, channel: Channel, snapshot) -> str:
        """Where this bus lands. Short, real, and never a routing path."""
        if channel.group == "mic":
            return "to " + ("stream" if channel.sink == "mic_b1" else "discord")
        if channel.sink == "laptop_audio":
            return "laptop in"
        return self._headset_target(snapshot)

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
        for group, tag in self.group_tags.items():
            tag.set_text(self._group_tag(group))
        self.share.apply(self.share_feeders, self.share_state.present)

    def _update_banner(self) -> None:
        missing = [
            c.name
            for c in CHANNELS
            if not self.states[c.sink].present and c.sink not in self.hidden
        ]
        if not self.share_state.present:
            missing.append("Share")
        if missing:
            names = ", ".join(missing)
            self.banner_text.set_text(f"{names} missing - the audio graph is not fully up")
        self.banner.set_visible(bool(missing))

    def _on_frame(self) -> bool:
        available = getattr(self.peaks, "available", True)
        for sink, strip in self.strips.items():
            level = self.peaks.level(f"{sink}.monitor") if available else 0.0
            strip.meter.feed(level)
            strip.meter.tick()
        for sink, sliver in self.slivers.items():
            level = self.peaks.level(f"{sink}.monitor") if available else 0.0
            sliver.feed(level, self.states[sink].muted)
        self.share.feed(self.peaks.level(f"{SHARE_SINK}.monitor") if available else 0.0)
        return True

    def _on_close(self, *_args) -> bool:
        self._save()
        self.shutdown()
        return False

    def shutdown(self) -> None:
        """Stop the pollers. ``parec`` leaks if the peak poller is not stopped."""
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

    def do_startup(self) -> None:
        Gtk.Application.do_startup(self)
        provider = Gtk.CssProvider()
        load_css(provider, style_mod.CSS(self.pal))
        display = Gdk.Display.get_default()
        if display is not None:
            Gtk.StyleContext.add_provider_for_display(
                display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )

    def do_activate(self) -> None:
        if self.window is None:
            self.window = MixerWindow(self)
        self.window.present()

    def do_shutdown(self) -> None:
        if self.window is not None:
            self.window.shutdown()
        Gtk.Application.do_shutdown(self)


def main(argv: list[str] | None = None) -> int:
    """Run the GTK4 mixer."""
    return MixerApp().run(argv if argv is not None else [])


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
