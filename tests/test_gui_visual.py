"""Render production GTK4 windows and save privacy-safe visual audit captures.

The windows are real Hearth widgets, but the mixer backend, IPC, systemd and
app-icon lookup are replaced by deterministic fixtures. The suite must never
connect to or mutate the developer's actual audio graph.
"""

from __future__ import annotations

import os
import time
import warnings
from pathlib import Path

import pytest


@pytest.mark.gui
def test_production_windows_render_and_capture(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Render loading, ready, missing-device and fault states plus both tool windows."""
    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import Gio, GLib, Gtk

    from hearth import legacy_core
    from hearth.ui.gtk4 import app as app_mod
    from hearth.ui.gtk4 import remap as remap_mod
    from hearth.ui.gtk4 import setup as setup_mod

    # Observe explicit accessible LABEL updates while preserving the actual
    # GTK property writes, so the test checks named controls rather than a
    # tooltip or neighboring visible text.
    accessible_labels: dict[int, list[str]] = {}
    original_update_property = Gtk.Accessible.update_property

    def record_accessible_label(widget, properties, values):
        if Gtk.AccessibleProperty.LABEL in properties:
            accessible_labels.setdefault(id(widget), []).append(str(values[0]))
        return original_update_property(widget, properties, values)

    monkeypatch.setattr(Gtk.Accessible, "update_property", record_accessible_label)

    # Isolate every host-facing boundary. This still constructs the real GTK
    # application/window/strip widgets, but no collector, meter, IPC, systemd,
    # pactl, MIDI, user config or installed script is read or written.
    monkeypatch.setattr(app_mod.MixerApp, "_start_control_socket", lambda _self: None)
    monkeypatch.setattr(app_mod.MixerWindow, "_start_backends", lambda _self: None)
    monkeypatch.setattr(app_mod.MixerWindow, "shutdown", lambda _self: None)
    monkeypatch.setattr(
        app_mod.settings_mod,
        "load",
        lambda: {
            "mixer_hidden": ["mic_b2"],
            "mixer_minimised": [],
            "mixer_order": ["vm_game", "vm_chat", "vm_music", "laptop_audio", "mic_b1", "mic_b2"],
            "mixer_rail_dock": "right",
        },
    )
    monkeypatch.setattr(app_mod.settings_mod, "save", lambda _values: None)
    # Keep the gallery deterministic and fast: app icon resolution has its
    # own tests; this suite is measuring the composition of UI widgets.
    icon_calls: dict[str, int] = {}

    def fake_icon_image(name: str, _size: int, **_kwargs):
        icon_calls[name] = icon_calls.get(name, 0) + 1
        # Make Firefox exercise the real fallback-holder replacement path:
        # first render is a letter chip, then the idle callback gets a glyph.
        if name == "Firefox" and icon_calls[name] == 1:
            return None
        image = Gtk.Image.new_from_icon_name("audio-x-generic-symbolic")
        image.set_accessible_role(Gtk.AccessibleRole.PRESENTATION)
        return image

    monkeypatch.setattr(app_mod.icons_mod, "icon_image", fake_icon_image)
    # Deterministically schedule the production install callback on GTK's main
    # loop; the separate cold-cache test below exercises real filesystem scans.
    monkeypatch.setattr(
        app_mod.icons_mod,
        "warm_indexes",
        lambda callback=None: GLib.idle_add(callback) if callback is not None else None,
    )
    monkeypatch.setattr(
        legacy_core,
        "read_conf",
        lambda *_args, **_kwargs: {
            "ASTRO_TARGET": legacy_core.ASTRO_CHAT,
            "LATENCY_MSEC": "12",
        },
    )
    monkeypatch.setattr(legacy_core, "moonlight_mic_source", lambda: "b1_mic")
    monkeypatch.setattr(setup_mod.SetupWindow, "_on_map", lambda _self, *_args: None)
    monkeypatch.setattr(setup_mod.SetupWindow, "_refresh_controller", lambda _self: None)
    monkeypatch.setattr(
        setup_mod.mic_routes,
        "read",
        lambda: {
            "B1_ROUTE": "sm7b_mono",
            "B1_ACTIVE": "1",
            "B2_ROUTE": "none",
            "B2_ACTIVE": "0",
            "CARLA_TO_B1": "1",
            "CARLA_TO_B2": "0",
        },
    )
    # Feed a synthetic map through the production renderer, while suppressing
    # its optional asynchronous systemd query.
    from hearth import lpd8map

    monkeypatch.setattr(
        remap_mod.lpd8map,
        "read_map",
        lambda: {
            "CC_VM_GAME": 6,
            "CC_VM_GAME_ALT": 0,
            "CC_VM_CHAT": 5,
            "CC_VM_MUSIC": 4,
            "CC_MIC_VOL": 7,
        },
    )
    monkeypatch.setattr(
        remap_mod.lpd8map,
        "read_pad_map",
        lambda: {slot.slot: slot.slot for slot in lpd8map.PAD_SLOTS},
    )
    monkeypatch.setattr(remap_mod.RemapWindow, "_queue_service_state_refresh", lambda _self: None)

    from PIL import Image, ImageChops, ImageGrab, ImageStat

    output = (
        Path(os.environ.get("HEARTH_UI_ARTIFACT_DIR", ""))
        if os.environ.get("HEARTH_UI_ARTIFACT_DIR")
        else tmp_path / "gui-captures"
    )
    output.mkdir(parents=True, exist_ok=True)

    def pump(seconds: float = 0.35) -> None:
        # The distro's PyGObject asyncio adapter emits one DeprecationWarning
        # per main-loop turn on this Python version. Suppress only warnings
        # originating in the external gi package during deterministic pumping;
        # keep application/test warnings visible.
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", category=DeprecationWarning, module=r"gi\.")
            context = GLib.MainContext.default()
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline:
                while context.pending():
                    context.iteration(False)
                time.sleep(0.005)

    captures: list[Path] = []

    def rendered_frame(name: str) -> Image.Image:
        desktop = ImageGrab.grab().convert("RGB")
        # Xvfb has no window manager, so GTK places each window at the root
        # origin and leaves the rest of the 1600x1200 desktop black. Trim that
        # area and keep a small dark margin so CI artifacts show the real
        # window rather than a tiny application in a sea of black pixels.
        bbox = ImageChops.difference(desktop, Image.new("RGB", desktop.size, (0, 0, 0))).getbbox()
        assert bbox is not None, f"{name} screenshot is completely black"
        left, top, right, bottom = bbox
        pad = 8
        return desktop.crop(
            (
                max(0, left - pad),
                max(0, top - pad),
                min(desktop.width, right + pad),
                min(desktop.height, bottom + pad),
            )
        )

    def capture(name: str, *, settle: float = 0.35) -> Path:
        pump(settle)
        image = rendered_frame(name)
        target = output / f"{name}.png"
        image.save(target, format="PNG", optimize=True)
        assert image.width >= 280 and image.height >= 100, (
            f"{name} capture bounds are implausible: {image.size}"
        )
        assert target.stat().st_size > 3_000, f"{name} screenshot looks empty"
        assert sum(ImageStat.Stat(image).stddev) > 8, f"{name} screenshot lacks visual content"
        captures.append(target)
        return target

    def assert_focus_changes_pixels(before: Image.Image, after_path: Path, name: str) -> None:
        after = Image.open(after_path).convert("RGB")
        assert before.size == after.size, (
            f"{name} focus capture changed layout size: {before.size} -> {after.size}"
        )
        difference = ImageChops.difference(before, after)
        assert difference.getbbox() is not None, f"{name} focus caused no visible pixel change"
        changed_pixels = sum(1 for pixel in difference.getdata() if max(pixel) >= 20)
        assert changed_pixels >= 20, (
            f"{name} focus cue is not visibly rendered ({changed_pixels} changed pixels)"
        )

    application = app_mod.MixerApp()
    # Use the production app identity but do not claim its D-Bus name; this
    # avoids accidentally forwarding activation to a desktop instance.
    application.set_flags(Gio.ApplicationFlags.NON_UNIQUE)
    assert application.register(None)
    application.activate()
    pump(0.6)
    window = application.window
    assert window is not None and window.get_mapped()

    game_out = window.strips["vm_game"].out_button
    mic_in = window.strips["mic_b1"].in_button
    assert game_out is not None and mic_in is not None
    game_out_name = f"Choose where {window.strips['vm_game'].channel.name} is sent"
    mic_in_name = f"Choose what feeds {window.strips['mic_b1'].channel.name}"
    assert game_out_name in accessible_labels[id(game_out)]
    assert mic_in_name in accessible_labels[id(mic_in)]

    # Initial state before any collector snapshot: strips are skeletons,
    # their controls are disabled, and the device text says it is checking.
    assert window.strips
    assert all("ghost" in strip.get_css_classes() for strip in window.strips.values())
    assert all(not strip.mute.get_sensitive() for strip in window.strips.values())
    assert (
        window.strips["vm_game"].minimise_button.get_tooltip_text() == "Minimise Game to the rail"
    )
    assert isinstance(window.strips["vm_game"].minimise_button.get_child(), Gtk.Image)
    assert (
        window.strips["vm_game"].minimise_button.get_child().get_accessible_role()
        == Gtk.AccessibleRole.PRESENTATION
    )
    assert window.strips["vm_game"].device.get_text() == "checking…"
    assert window.strips["vm_game"].value.get_text() == "…"
    capture("mixer-loading")

    # Deliver an entirely synthetic snapshot to the actual widgets.
    levels = {
        "vm_game": 76,
        "vm_chat": 58,
        "vm_music": 29,
        "laptop_audio": 42,
        "mic_b1": 83,
        "mic_b2": 61,
    }
    devices = {
        "vm_game": "A50 game",
        "vm_chat": "A50 chat",
        "vm_music": "A50 chat",
        "laptop_audio": "Laptop output",
        "mic_b1": "Carla / SM7B",
        "mic_b2": "Spare microphone",
    }
    for sink, state in window.states.items():
        state.seen = True
        state.present = True
        state.volume = levels[sink]
        state.device = devices[sink]
        state.muted = sink == "vm_chat"
        state.listeners = []
    window.states["vm_game"].listeners = [
        app_mod.Listener("Firefox", muted=False, index="fixture-101", volume=72),
        app_mod.Listener("Vesktop", muted=True, index="fixture-102", volume=24),
    ]
    window.states["vm_chat"].listeners = [
        app_mod.Listener("Vesktop", muted=False, index="fixture-103", volume=67),
    ]
    window._routes_known = True
    window._routes = {
        "vm_game.monitor": {"alsa_output.usb-Astro_Gaming_Astro_A50-00.stereo-game"},
        "vm_chat.monitor": {"alsa_output.usb-Astro_Gaming_Astro_A50-00.stereo-chat"},
    }
    window._sources_known = True
    window._sources_present = {"sm7b_mono", "astro_mic_48k"}
    window.apply_states()
    pump(0.45)
    assert "ghost" not in window.strips["vm_game"].get_css_classes()
    assert window.strips["vm_game"].mute.get_sensitive()
    assert window.strips["vm_game"].value.get_text() == "76"
    assert window.strips["vm_game"].listeners.get_first_child() is not None
    firefox_row = window.strips["vm_game"].listeners.get_first_child()
    assert firefox_row is not None
    firefox_icon_holder = firefox_row.get_first_child()
    assert firefox_icon_holder is not None
    assert isinstance(firefox_icon_holder.get_first_child(), Gtk.Image)
    assert icon_calls.get("Firefox", 0) >= 2  # fallback then real icon
    ready_path = capture("mixer-ready")
    ready_frame = Image.open(ready_path).convert("RGB")

    # Capture a real keyboard-focus state as visual evidence, not just a
    # focusable-property assertion. Clear focus again so later states remain
    # representative of their default appearance.
    focused_fader = window.strips["vm_game"].fader
    assert focused_fader.grab_focus()
    pump(0.02)
    assert focused_fader.has_focus()
    fader_focus_path = capture("mixer-fader-focus", settle=0.0)
    assert_focus_changes_pixels(ready_frame, fader_focus_path, "volume fader")
    window.set_focus(None)

    # A confirmed missing endpoint is different from loading: it must stop
    # looking like a skeleton and must not allow actions on a vanished bus.
    window.states["laptop_audio"].present = False
    window.apply_states()
    assert "ghost" not in window.strips["laptop_audio"].get_css_classes()
    assert "dead" in window.strips["laptop_audio"].get_css_classes()
    assert window.strips["laptop_audio"].device.get_text() == "no device"
    assert not window.strips["laptop_audio"].mute.get_sensitive()
    capture("mixer-missing-device")

    # The repair banner is another meaningful GUI state. Set fixture text
    # directly; never click Restart or invoke the real systemd path.
    window.banner_text.set_text("Audio routing needs attention")
    window.banner_units = ("roaring-vm-sinks.service",)
    window.banner.set_visible(True)
    capture("mixer-fault-banner")

    window.set_visible(False)
    accessible_names: list[tuple[Gtk.Widget, str]] = []
    original_accessible_label = setup_mod.action_icons.accessible_label

    def track_accessible_label(widget: Gtk.Widget, label: str) -> None:
        original_accessible_label(widget, label)
        accessible_names.append((widget, label))

    monkeypatch.setattr(setup_mod.action_icons, "accessible_label", track_accessible_label)
    setup_window = setup_mod.SetupWindow(window)
    assert any(
        widget is setup_window.latency and label == "Routing latency in milliseconds"
        for widget, label in accessible_names
    )
    assert setup_window.latency.get_accessible_role() == Gtk.AccessibleRole.SPIN_BUTTON
    setup_window.flow.set_text("SM7B → Carla → Mic B1\nMic B1 → voice apps\nMic B2 → spare input")
    setup_window.lpd8_state.set_text("Controller status is checked when Setup opens")
    setup_window.lpd8_map.set_text("K5 · Music    K6 · Chat    K7 · Game    K8 · Microphone")
    setup_window.present()
    pump()
    assert setup_window.get_mapped()
    assert setup_window.save_devices_label.get_text() == "Save"
    assert isinstance(setup_window.save_devices.get_child(), Gtk.Box)
    assert isinstance(setup_window.save_devices.get_child().get_first_child(), Gtk.Image)
    assert (
        setup_window.save_devices.get_child().get_first_child().get_accessible_role()
        == Gtk.AccessibleRole.PRESENTATION
    )
    assert setup_window.reconnect_lpd8.get_tooltip_text().startswith("Restart lpd8-mixer")
    assert isinstance(setup_window.reconnect_lpd8.get_child(), Gtk.Box)
    assert isinstance(setup_window.remap_lpd8.get_child(), Gtk.Box)
    setup_mod.action_icons.set_label(
        setup_window.save_devices,
        setup_window.save_devices_label,
        "Saving…",
    )
    assert setup_window.save_devices_label.get_text() == "Saving…"
    assert isinstance(setup_window.save_devices.get_child().get_first_child(), Gtk.Image)
    setup_mod.action_icons.set_label(
        setup_window.save_devices,
        setup_window.save_devices_label,
        "Save",
    )
    capture("setup")

    setup_window.set_visible(False)
    remap_window = remap_mod.RemapWindow(window)
    remap_window.present()
    pump()
    assert remap_window.get_mapped()
    assert len(remap_window._pads) == 8 and len(remap_window._knobs) == 8
    assert remap_window._map_usable
    capture("lpd8-controls")

    # The minimized rail uses a custom-drawn control too. Include a focused
    # example in the artifact so the visual reviewer can inspect its focus ring.
    # Restore a normal device/banner state first, so the focus evidence is not
    # confused with the previous missing-device/fault styling captures.
    window.states["laptop_audio"].present = True
    window.apply_states()
    window.banner_units = ()
    window.banner_text.set_text("")
    window.banner.set_visible(False)
    setup_window.set_visible(False)
    remap_window.set_visible(False)
    window.present()
    pump(0.05)
    window.minimise("vm_music")
    # Let the window's existing size-ease animation finish before the reference
    # frame, otherwise a later frame can resize by a few pixels and mask a focus
    # ring comparison.
    pump(0.4)
    focused_sliver = window.slivers["vm_music"]
    assert focused_sliver.get_accessible_role() == Gtk.AccessibleRole.BUTTON
    # Establish a same-layout comparison frame with no focused rail item.
    window.set_focus(None)
    pump(0.05)
    unfocused_rail = rendered_frame("mixer-rail-unfocused-reference")
    assert focused_sliver.grab_focus()
    pump(0.02)
    assert focused_sliver.has_focus()
    rail_focus_path = capture("mixer-rail-focus", settle=0.0)
    assert_focus_changes_pixels(unfocused_rail, rail_focus_path, "minimized rail")

    # Restore Music, then show the same visible focus cue on the strip-order
    # handle; keyboard reordering is a separate operation from slider focus.
    window.restore("vm_music")
    pump(0.4)
    reorder_grip = window.strips["vm_game"].reorder_handle
    assert reorder_grip.get_focusable()
    assert reorder_grip.get_accessible_role() == Gtk.AccessibleRole.GROUP
    window.set_focus(None)
    pump(0.05)
    unfocused_grip = rendered_frame("mixer-reorder-grip-unfocused-reference")
    assert reorder_grip.grab_focus()
    pump(0.02)
    assert reorder_grip.has_focus()
    grip_focus_path = capture("mixer-reorder-grip-focus", settle=0.0)
    assert_focus_changes_pixels(unfocused_grip, grip_focus_path, "strip reorder grip")

    # Stable evidence: the artifacts are all raster screenshots from the Xvfb
    # root window and contain the expected sample of production GUI states.
    assert {path.name for path in captures} == {
        "mixer-loading.png",
        "mixer-ready.png",
        "mixer-fader-focus.png",
        "mixer-rail-focus.png",
        "mixer-reorder-grip-focus.png",
        "mixer-missing-device.png",
        "mixer-fault-banner.png",
        "setup.png",
        "lpd8-controls.png",
    }

    # Exercise keyboard reordering against the real production window, not only
    # a fake object: reorder rebuilds every strip, so focus must land on the
    # newly created grip and survive another arrow press.
    original_order = list(window.order)
    original_grip = window.strips["vm_game"].reorder_handle
    assert window.strips["vm_game"]._on_grip_key_pressed(
        None, app_mod.Gdk.KEY_Right, 0, app_mod.Gdk.ModifierType(0)
    )
    assert window.order.index("vm_game") == original_order.index("vm_game") + 1
    pump(0.05)
    assert window.strips["vm_game"].reorder_handle is not original_grip
    assert window.strips["vm_game"].reorder_handle.has_focus()
    assert window.strips["vm_game"]._on_grip_key_pressed(
        None, app_mod.Gdk.KEY_Left, 0, app_mod.Gdk.ModifierType(0)
    )
    assert window.order == original_order
    pump(0.05)
    assert window.strips["vm_game"].reorder_handle.has_focus()

    setup_window.set_visible(False)
    remap_window.set_visible(False)
    window.set_visible(False)
    application.quit()


@pytest.mark.gui
def test_cold_icon_indexing_stays_off_the_gtk_thread() -> None:
    """A cold rare-icon lookup must return immediately and warm indexes in a worker."""
    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import GLib

    from hearth.ui.gtk4 import icons

    icons._entry_index.cache_clear()
    icons._file_index.cache_clear()
    # Reset the worker bookkeeping as well as lru_caches so this test is
    # deterministic if another GUI test warmed icons in this same process.
    with icons._INDEX_LOCK:
        icons._INDEX_READY = False
        icons._INDEX_BUILDING = False
        icons._INDEX_CALLBACKS.clear()
    assert icons.icon_image("hearth-icon-that-does-not-exist", 12, allow_index_scan=False) is None
    assert icons._entry_index.cache_info().currsize == 0
    assert icons._file_index.cache_info().currsize == 0

    finished: list[bool] = []
    icons.warm_indexes(lambda: finished.append(True) or False)
    context = GLib.MainContext.default()
    deadline = time.monotonic() + 15.0
    while not finished and time.monotonic() < deadline:
        while context.pending():
            context.iteration(False)
        time.sleep(0.01)

    assert finished, "background icon index did not report back on the GTK main loop"
    assert icons._entry_index.cache_info().currsize == 1
    assert icons._file_index.cache_info().currsize == 1


@pytest.mark.gui
def test_custom_fader_supports_keyboard_and_exposes_slider_role() -> None:
    """The drawn fader behaves like a value control without pointer input."""
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Gdk", "4.0")
    from gi.repository import Gdk, Gtk

    from hearth.ui.gtk4.widgets import VMAX, Fader

    palette = {
        "slot": "#202020",
        "accent": "#80aaff",
        "cap": "#d0d0d0",
        "cap-groove": "#404040",
    }
    fader = Fader(palette)
    assert fader.get_focusable()
    assert fader.get_accessible_role() == Gtk.AccessibleRole.SLIDER

    moved: list[int] = []
    fader.connect("moved", lambda _widget, value: moved.append(value))
    fader.set_value(50)
    assert fader._on_key_pressed(None, Gdk.KEY_Up, 0, Gdk.ModifierType(0))
    assert fader.value == 51
    assert fader._on_key_pressed(None, Gdk.KEY_Page_Up, 0, Gdk.ModifierType(0))
    assert fader.value == 61
    assert fader._on_key_pressed(None, Gdk.KEY_Home, 0, Gdk.ModifierType(0))
    assert fader.value == 0
    assert fader._on_key_pressed(None, Gdk.KEY_End, 0, Gdk.ModifierType(0))
    assert fader.value == VMAX
    assert moved == [51, 61, 0, VMAX]

    fader.set_dead(True)
    assert not fader.get_sensitive()
    assert not fader._on_key_pressed(None, Gdk.KEY_Up, 0, Gdk.ModifierType(0))
    assert not fader._on_scroll(None, 0.0, 1.0)
    assert fader.value == VMAX
    fader.set_dead(False)
    assert fader.get_sensitive()


@pytest.mark.gui
def test_lpd8_pad_and_knob_accessible_names_track_assignment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hardware-shaped buttons expose their number and current assignment."""
    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk

    from hearth.ui.gtk4 import remap as remap_mod

    observed: list[tuple[Gtk.Accessible, str]] = []
    update_property = Gtk.Accessible.update_property

    def record_property(widget, properties, values):
        if Gtk.AccessibleProperty.LABEL in properties:
            observed.append((widget, str(values[0])))
        return update_property(widget, properties, values)

    monkeypatch.setattr(Gtk.Accessible, "update_property", record_property)
    pad = remap_mod._Pad(3, lambda _control: None)
    knob = remap_mod._Knob(5, remap_mod.style_mod.palette(), lambda _control: None)
    assert pad.get_accessible_role() == Gtk.AccessibleRole.BUTTON
    assert knob.get_accessible_role() == Gtk.AccessibleRole.BUTTON
    assert (pad, "LPD8 pad 3; unassigned") in observed
    assert (knob, "LPD8 knob 5; unassigned") in observed

    pad.show_job("Mute Game", "Pad 3 toggles the Game bus")
    knob.show_job("Game volume", "Knob 5 adjusts Game volume")
    assert (pad, "LPD8 pad 3: Mute Game") in observed
    assert (knob, "LPD8 knob 5: Game volume") in observed

    # Empty assignments must also be announced clearly, not left with stale
    # names from the previous controller map.
    pad.show_job("", "Pad 3 does nothing yet")
    knob.show_job("", "Knob 5 moves nothing yet")
    assert (pad, "LPD8 pad 3; unassigned") in observed
    assert (knob, "LPD8 knob 5; unassigned") in observed


@pytest.mark.gui
def test_minimised_sliver_is_a_keyboard_accessible_restore_button() -> None:
    """Compact channel slivers can be focused and activated without a pointer."""
    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk

    from hearth.ui.gtk4.widgets import Sliver

    palette = {
        "view-alt": "#202020",
        "bad": "#ff5555",
        "ok": "#55ff55",
        "fg-dim": "#aaaaaa",
        "fg": "#eeeeee",
    }
    sliver = Sliver(palette, "Game", 56)
    assert isinstance(sliver, Gtk.Button)
    assert sliver.get_focusable()
    assert sliver.get_accessible_role() == Gtk.AccessibleRole.BUTTON

    activations: list[bool] = []
    sliver.connect("clicked", lambda *_args: activations.append(True))
    # Activation belongs to the native button, preserving GTK's standard
    # keyboard and assistive-technology action instead of a simulated key path.
    sliver.emit("clicked")
    assert activations == [True]
    assert sliver.get_child().get_accessible_role() == Gtk.AccessibleRole.PRESENTATION
