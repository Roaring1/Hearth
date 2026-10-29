"""Safety tests for the developer's live functional-check harness.

The harness is intentionally GTK4-marked, but all pactl interactions here are
mocked. The tests prove cleanup preserves user state without changing a real
sink or needing a running audio graph.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import threading
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

pytest.importorskip("gi")
pytestmark = pytest.mark.gui


def _load_func_check() -> ModuleType:
    script = Path(__file__).resolve().parents[1] / "scripts" / "func_check.py"
    spec = importlib.util.spec_from_file_location("hearth_func_check_test", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _load_mixer() -> ModuleType:
    from hearth.ui.gtk4 import app

    return app


def _load_setup() -> ModuleType:
    from hearth.ui.gtk4 import setup

    return setup


def _fake_pactl(calls: list[list[str]]):
    def run(command, **_kwargs):
        argv = list(command)
        if argv[:2] == ["pactl", "get-sink-volume"]:
            return subprocess.CompletedProcess(
                argv,
                0,
                stdout=(
                    "Volume: front-left: 32768 / 50% / -6.00 dB, "
                    "front-right: 49152 / 75% / -2.50 dB"
                ),
                stderr="",
            )
        if argv[:2] == ["pactl", "get-sink-mute"]:
            return subprocess.CompletedProcess(argv, 0, stdout="Mute: yes\n", stderr="")
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    return run


def test_listener_rows_are_not_rebuilt_for_identical_snapshots() -> None:
    mixer = _load_mixer()
    listeners = [SimpleNamespace(index="42", name="Firefox", muted=False, volume=63)]
    signature = (("42", "Firefox", False),)
    listener_box = object()
    cleared = []
    synced = []
    strip = SimpleNamespace(
        _listener_signature=signature,
        listeners=listener_box,
        _clear=lambda box: cleared.append(box),
        _sync_listener_volumes=lambda current: synced.append(current),
    )

    mixer.Strip._fill_listeners(strip, listeners)

    assert cleared == []
    assert synced == [listeners]


def test_app_mute_cache_reconciles_a_stale_backend_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A successful launch must not pin the optimistic mute state in the UI."""
    mixer = _load_mixer()
    monkeypatch.setattr(mixer.pactl, "set_stream_mute", lambda *_args: True)
    monkeypatch.setattr(mixer, "_set_mute_icon", lambda *_args: None)
    cleared: list[object] = []

    class FakeWidget:
        def __init__(self, *args, **kwargs) -> None:
            self.children: list[FakeWidget] = []
            self.classes: set[str] = set()
            self.parent = None

        def __getattr__(self, _name):
            return lambda *_args, **_kwargs: None

        def append(self, child) -> None:
            self.children.append(child)
            child.parent = self

        def get_parent(self):
            return self.parent

        def get_first_child(self):
            return self.children[0] if self.children else None

        def get_next_sibling(self):
            if self.parent is None:
                return None
            try:
                index = self.parent.children.index(self)
            except ValueError:
                return None
            return (
                self.parent.children[index + 1] if index + 1 < len(self.parent.children) else None
            )

        def add_css_class(self, name: str) -> None:
            self.classes.add(name)

        def remove_css_class(self, name: str) -> None:
            self.classes.discard(name)

        def set_tooltip_text(self, _tooltip: str | None) -> None:
            pass

        def update_property(self, _properties, values) -> None:
            self.accessible_label = values[0]

    class FakeScale(FakeWidget):
        @classmethod
        def new_with_range(cls, *_args):
            return cls()

    monkeypatch.setattr(mixer.Gtk, "Box", FakeWidget)
    monkeypatch.setattr(mixer.Gtk, "Label", FakeWidget)
    monkeypatch.setattr(mixer.Gtk, "Button", FakeWidget)
    monkeypatch.setattr(mixer.Gtk, "Scale", FakeScale)
    monkeypatch.setattr(mixer.Gtk, "DragSource", FakeWidget)
    monkeypatch.setattr(
        mixer.Strip, "_clear", lambda _self, box: (cleared.append(box), box.children.clear())
    )
    monkeypatch.setattr(mixer.Strip, "_mark", lambda _self, _name, _size: FakeWidget())
    monkeypatch.setattr(mixer.Strip, "_sync_listener_volumes", lambda _self, _items: None)

    initial = mixer.Listener(name="Firefox", muted=False, index="42", volume=63)
    strip = SimpleNamespace(
        channel=SimpleNamespace(group="audio"),
        listeners=FakeWidget(),
        _listener_signature=None,
        _listener_volume_controls={},
        _listener_volume_held={},
        _on_app_volume=lambda *_args: None,
        _on_app_mute=lambda *_args: None,
        _on_listener_name_key_pressed=lambda *_args: False,
    )
    strip._clear = lambda box: mixer.Strip._clear(strip, box)
    strip._mark = lambda name, size: mixer.Strip._mark(strip, name, size)
    strip._sync_listener_volumes = lambda items: mixer.Strip._sync_listener_volumes(strip, items)

    mixer.Strip._fill_listeners(strip, [initial])
    original_row = strip.listeners.children[0]
    volume_slider = original_row.children[2]
    mute_button = original_row.children[4]
    name_label = original_row.children[1]
    assert volume_slider.accessible_label == "Firefox volume"
    assert mute_button.accessible_label == "Mute Firefox"

    mixer.Strip._on_app_mute(strip, mute_button, initial)
    assert initial.muted is True
    assert strip._listener_signature == (("42", "Firefox", True),)
    assert mute_button.accessible_label == "Unmute Firefox"
    assert "muted" in name_label.classes

    # A stale backend snapshot must rebuild the row to its observed state.
    stale_snapshot = mixer.Listener(name="Firefox", muted=False, index="42", volume=63)
    mixer.Strip._fill_listeners(strip, [stale_snapshot])
    assert len(cleared) == 2
    assert strip._listener_signature == (("42", "Firefox", False),)
    assert strip.listeners.children[0] is not original_row
    assert strip.listeners.children[0].children[4].accessible_label == "Mute Firefox"
    assert "muted" not in strip.listeners.children[0].children[1].classes


def test_rail_dock_button_accessible_name_describes_its_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mixer = _load_mixer()

    class FakeWidget:
        def __init__(self) -> None:
            self.accessible_label = ""
            self.label = ""
            self.tooltip = ""

        def __getattr__(self, _name):
            return lambda *_args, **_kwargs: None

        def update_property(self, _properties, values) -> None:
            self.accessible_label = values[0]

        def set_label(self, label: str) -> None:
            self.label = label

        def set_tooltip_text(self, tooltip: str) -> None:
            self.tooltip = tooltip

    strip = SimpleNamespace(rail_dock="right", rail=FakeWidget(), rail_handle=FakeWidget())
    mixer.MixerWindow._apply_rail_dock(strip)
    assert strip.rail_handle.accessible_label == "Stack minimised channels along the bottom"
    assert strip.rail_handle.tooltip == strip.rail_handle.accessible_label

    strip.rail_dock = "bottom"
    mixer.MixerWindow._apply_rail_dock(strip)
    assert strip.rail_handle.accessible_label == "Stack minimised channels down the right"


def test_strip_grip_keyboard_reorders_visible_strips_and_restores_focus(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mixer = _load_mixer()
    Gdk, Gtk = mixer.Gdk, mixer.Gtk

    class FakeWindow:
        def __init__(self) -> None:
            self.order = ["vm_game", "vm_chat", "vm_music", "laptop_audio", "mic_b1"]
            self.strips = {sink: object() for sink in self.order[:4]}
            self.rebuild_count = 0
            self.save_count = 0
            self.focused: list[str] = []

        def _channel(self, sink):
            return mixer.MixerWindow._channel(self, sink)

        def rebuild(self) -> None:
            self.rebuild_count += 1

        def _save(self) -> None:
            self.save_count += 1

        def _focus_reorder_handle(self, sink: str) -> bool:
            self.focused.append(sink)
            return False

        def reorder(self, moved: str, target: str) -> None:
            mixer.MixerWindow.reorder(self, moved, target)

    window = FakeWindow()
    queued: list[tuple[object, tuple[object, ...]]] = []
    monkeypatch.setattr(
        mixer.GLib, "idle_add", lambda callback, *args: queued.append((callback, args)) or 1
    )
    strip = SimpleNamespace(channel=mixer.MixerWindow._channel(window, "vm_game"), win=window)
    grip_owner = SimpleNamespace(
        channel=strip.channel,
        pal={"button": "#888888"},
        _on_grip_key_pressed=lambda *args: mixer.Strip._on_grip_key_pressed(strip, *args),
    )
    grip = mixer.Strip._grip(grip_owner)
    assert isinstance(grip, Gtk.DrawingArea)
    assert grip.get_focusable()
    assert grip.get_accessible_role() == Gtk.AccessibleRole.GROUP
    assert "Left/Right" in grip.get_tooltip_text()

    # A keyboard move uses the same reorder/save implementation as drag-drop.
    assert mixer.Strip._on_grip_key_pressed(strip, None, Gdk.KEY_Right, 0, Gdk.ModifierType(0))
    assert window.order[:4] == ["vm_chat", "vm_game", "vm_music", "laptop_audio"]
    assert window.rebuild_count == 1 and window.save_count == 1
    assert queued
    callback, args = queued.pop(0)
    callback(*args)
    assert window.focused == ["vm_game"]

    # Left returns it, boundaries are handled without an out-of-range move,
    # and unrelated keys remain available to normal GTK navigation.
    assert mixer.Strip._on_grip_key_pressed(strip, None, Gdk.KEY_Left, 0, Gdk.ModifierType(0))
    assert window.order[:4] == ["vm_game", "vm_chat", "vm_music", "laptop_audio"]
    assert mixer.Strip._on_grip_key_pressed(strip, None, Gdk.KEY_Left, 0, Gdk.ModifierType(0))
    assert window.order[:4] == ["vm_game", "vm_chat", "vm_music", "laptop_audio"]
    assert not mixer.Strip._on_grip_key_pressed(strip, None, Gdk.KEY_Escape, 0, Gdk.ModifierType(0))


def test_per_app_volume_callback_queues_stream_not_bus(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mixer = _load_mixer()
    calls: list[tuple[str, str, int]] = []

    def queue_volume(kind, target, percent, _on_failure):
        calls.append((kind, target, percent))
        return True

    class FakeScale:
        def __init__(self) -> None:
            self.value = 73
            self.tooltip = ""

        def get_value(self) -> int:
            return self.value

        def set_tooltip_text(self, text: str) -> None:
            self.tooltip = text

    class FakeLabel:
        text = ""

        def set_text(self, text: str) -> None:
            self.text = text

    class FakeCollector:
        refreshes = 0

        def refresh_now(self) -> None:
            self.refreshes += 1

    scale = FakeScale()
    label = FakeLabel()
    collector = FakeCollector()
    strip = SimpleNamespace(
        _listener_volume_held={},
        _listener_volume_controls={"42": (scale, label, 1)},
        win=SimpleNamespace(collector=collector, queue_volume=queue_volume),
    )
    listener = SimpleNamespace(index="42", name="Firefox")

    mixer.Strip._on_app_volume(strip, scale, listener)

    assert calls == [("stream", "42", 73)]
    assert label.text == "73%"
    assert scale.tooltip == "Firefox: 73%"
    assert strip._listener_volume_held["42"] > 0
    assert collector.refreshes == 0


def test_volume_worker_coalesces_rapid_updates_per_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mixer = _load_mixer()
    workers = []
    sent: list[tuple[str, str, int]] = []
    failures: list[str] = []

    class CapturedThread:
        def __init__(self, *, target, **_kwargs) -> None:
            self.target = target

        def start(self) -> None:
            workers.append(self)

    monkeypatch.setattr(mixer.threading, "Thread", CapturedThread)
    monkeypatch.setattr(mixer.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(mixer.GLib, "idle_add", lambda callback: callback())

    monkeypatch.setattr(
        mixer.pactl,
        "set_volume",
        lambda sink, percent: sent.append(("sink", sink, percent)) or True,
    )
    monkeypatch.setattr(
        mixer.pactl,
        "set_stream_volume",
        lambda index, percent: sent.append(("stream", index, percent)) or True,
    )
    window = SimpleNamespace(
        _volume_lock=threading.Lock(),
        _volume_pending={},
        _volume_worker_active=False,
    )
    window._drain_volume_updates = lambda: mixer.MixerWindow._drain_volume_updates(window)

    def queue(kind: str, target: str, percent: int) -> bool:
        return mixer.MixerWindow.queue_volume(
            window, kind, target, percent, lambda: failures.append(target)
        )

    assert queue("sink", "vm_game", 20)
    assert queue("sink", "vm_game", 64)
    assert queue("stream", "42", 15)
    assert queue("stream", "42", 77)
    # Different targets coalesce independently; only one worker is launched.
    assert len(workers) == 1
    assert sent == []

    workers[0].target()

    assert sent == [("sink", "vm_game", 64), ("stream", "42", 77)]
    assert failures == []
    assert window._volume_pending == {}
    assert window._volume_worker_active is False


def test_volume_worker_reports_deferred_pactl_launch_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mixer = _load_mixer()
    workers = []
    failures: list[str] = []

    class CapturedThread:
        def __init__(self, *, target, **_kwargs) -> None:
            self.target = target

        def start(self) -> None:
            workers.append(self)

    monkeypatch.setattr(mixer.threading, "Thread", CapturedThread)
    monkeypatch.setattr(mixer.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(mixer.GLib, "idle_add", lambda callback: callback())
    monkeypatch.setattr(mixer.pactl, "set_volume", lambda *_args: False)

    window = SimpleNamespace(
        _volume_lock=threading.Lock(),
        _volume_pending={},
        _volume_worker_active=False,
    )
    window._drain_volume_updates = lambda: mixer.MixerWindow._drain_volume_updates(window)

    assert mixer.MixerWindow.queue_volume(
        window, "sink", "vm_game", 64, lambda: failures.append("vm_game")
    )
    workers[0].target()

    assert failures == ["vm_game"]
    assert window._volume_worker_active is False


def test_volume_worker_recovers_if_worker_thread_cannot_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mixer = _load_mixer()

    class ThreadThatCannotStart:
        def __init__(self, **_kwargs) -> None:
            pass

        def start(self) -> None:
            raise RuntimeError("thread limit reached")

    monkeypatch.setattr(mixer.threading, "Thread", ThreadThatCannotStart)
    window = SimpleNamespace(
        _volume_lock=threading.Lock(),
        _volume_pending={},
        _volume_worker_active=False,
    )
    window._drain_volume_updates = lambda: mixer.MixerWindow._drain_volume_updates(window)

    assert mixer.MixerWindow.queue_volume(window, "sink", "vm_game", 64, lambda: None) is False
    assert window._volume_pending == {}
    assert window._volume_worker_active is False


def test_listener_volume_snapshot_sync_respects_drag_hold_and_blocks_signal() -> None:
    mixer = _load_mixer()

    class FakeScale:
        def __init__(self, value: int) -> None:
            self.value = value
            self.blocked = False
            self.tooltip = ""

        def get_value(self) -> int:
            return self.value

        def set_value(self, value: int) -> None:
            self.value = value

        def handler_block(self, _handler: int) -> None:
            self.blocked = True

        def handler_unblock(self, _handler: int) -> None:
            self.blocked = False

        def set_tooltip_text(self, text: str) -> None:
            self.tooltip = text

    class FakeLabel:
        text = ""

        def set_text(self, text: str) -> None:
            self.text = text

    scale, label = FakeScale(40), FakeLabel()
    strip = SimpleNamespace(
        _listener_volume_controls={"42": (scale, label, 4)},
        _listener_volume_held={},
    )
    listener = SimpleNamespace(index="42", name="Firefox", volume=72)

    mixer.Strip._sync_listener_volumes(strip, [listener])

    assert scale.value == 72
    assert scale.blocked is False
    assert label.text == "72%"
    assert scale.tooltip == "Firefox: 72%"

    scale.value = 88
    strip._listener_volume_held["42"] = 10**12
    mixer.Strip._sync_listener_volumes(strip, [listener])
    assert scale.value == 88
    assert label.text == "72%"


def test_volume_snapshot_keeps_all_channel_levels(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    module = _load_func_check()
    calls: list[list[str]] = []
    monkeypatch.setattr(module.subprocess, "run", _fake_pactl(calls))

    assert module.volumes() == [50, 75]
    assert module.vol() == 50
    assert module.muted() == "yes"
    assert calls == []


def test_audio_restore_uses_original_channel_volumes_and_mute(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    module = _load_func_check()
    calls: list[list[str]] = []
    monkeypatch.setattr(module.subprocess, "run", _fake_pactl(calls))
    app = module.FuncApp()

    app.restore_audio_state()

    assert calls == [
        ["pactl", "set-sink-volume", "vm_game", "50%", "75%"],
        ["pactl", "set-sink-mute", "vm_game", "1"],
    ]


def test_live_audio_mutation_is_refused_without_complete_snapshot() -> None:
    module = _load_func_check()

    assert module.can_restore_audio_state([50, 75], "yes")
    assert module.can_restore_audio_state([50], "no")
    assert not module.can_restore_audio_state([], "yes")
    assert not module.can_restore_audio_state([50], "unknown")


def test_route_settled_reconciles_open_checkboxes_to_the_actual_graph(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mixer = _load_mixer()

    class FakeCheck:
        def __init__(self, active: bool) -> None:
            self.active = active
            self.tooltip = "stale failure"

        def set_active(self, active: bool) -> None:
            self.active = active

        def set_tooltip_text(self, tooltip: str | None) -> None:
            self.tooltip = tooltip

    class FakeCollector:
        refreshes = 0

        def refresh_now(self) -> None:
            self.refreshes += 1

    class InlineThread:
        def __init__(self, *, target, **_kwargs) -> None:
            self.target = target

        def start(self) -> None:
            self.target()

    monkeypatch.setattr(mixer.threading, "Thread", InlineThread)
    monkeypatch.setattr(mixer.GLib, "idle_add", lambda callback, *args: callback(*args))
    live_modules = [
        mixer.pactl.Module(
            index=55,
            name="module-loopback",
            args="source=vm_game.monitor sink=vm_chat",
        ),
        mixer.pactl.Module(
            index=56,
            name="module-loopback",
            args="source=vm_game.monitor-extra sink=vm_music",
        ),
    ]
    monkeypatch.setattr(mixer.pactl, "modules", lambda **_kwargs: live_modules)
    collector = FakeCollector()
    strip = SimpleNamespace(
        win=SimpleNamespace(collector=collector),
        channel=SimpleNamespace(sink="vm_game"),
        _out_checks={
            "vm_chat": FakeCheck(False),
            "vm_music": FakeCheck(True),
        },
        _routing=False,
        _route_busy=False,
        _route_errors={},
        _route_generation=0,
    )
    strip._apply_live_routes = lambda *args: mixer.Strip._apply_live_routes(strip, *args)
    strip._apply_route_snapshot = lambda *args: mixer.Strip._apply_route_snapshot(strip, *args)

    assert mixer.Strip._route_settled(strip) is False
    assert collector.refreshes == 1
    assert strip._out_checks["vm_chat"].active is True
    assert strip._out_checks["vm_music"].active is False
    assert all(check.tooltip is None for check in strip._out_checks.values())
    assert strip._routing is False


def test_output_popover_uses_cached_routes_without_pactl_io(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mixer = _load_mixer()

    class FakeBox:
        def __init__(self, **_kwargs) -> None:
            self.children = []

        def append(self, child) -> None:
            self.children.append(child)

    class FakeCheck:
        def __init__(self, *, label: str) -> None:
            self.label = label
            self.active = False
            self.sensitive = True
            self.tooltip = None

        def set_active(self, active: bool) -> None:
            self.active = active

        def set_sensitive(self, sensitive: bool) -> None:
            self.sensitive = sensitive

        def set_tooltip_text(self, tooltip: str | None) -> None:
            self.tooltip = tooltip

        def connect(self, *_args) -> None:
            pass

    class FakePopover:
        child = None

        def set_child(self, child) -> None:
            self.child = child

    monkeypatch.setattr(mixer.Gtk, "Box", FakeBox)
    monkeypatch.setattr(mixer.Gtk, "CheckButton", FakeCheck)
    monkeypatch.setattr(
        mixer.pactl,
        "modules",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("GTK callback spawned Pactl")),
    )
    strip = SimpleNamespace(
        win=SimpleNamespace(
            _routes={
                "vm_game.monitor": {
                    "vm_chat",
                    "alsa_output.usb-Astro_Gaming_Astro_A50-00.stereo-chat",
                }
            },
            _routes_known=True,
        ),
        channel=SimpleNamespace(sink="vm_game", group="headset"),
        out_popover=FakePopover(),
        _route_busy=False,
        _route_errors={},
        _on_route=lambda *_args: None,
    )

    mixer.Strip._fill_outputs(strip)

    checks = {check.label: check for check in strip._out_checks.values()}
    assert checks["A50 chat"].active is True
    assert all(check.sensitive for check in checks.values())
    assert strip.out_popover.child.children


def test_opening_output_popover_during_route_job_defers_refresh_until_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mixer = _load_mixer()

    class FakeBox:
        def __init__(self, **_kwargs) -> None:
            self.children = []

        def append(self, child) -> None:
            self.children.append(child)

    class FakeCheck:
        def __init__(self, *, label: str) -> None:
            self.label = label
            self.active = False
            self.sensitive = True
            self.tooltip = None

        def set_active(self, active: bool) -> None:
            self.active = active

        def set_sensitive(self, sensitive: bool) -> None:
            self.sensitive = sensitive

        def set_tooltip_text(self, tooltip: str | None) -> None:
            self.tooltip = tooltip

        def connect(self, *_args) -> None:
            pass

    class FakePopover:
        child = None

        def set_child(self, child) -> None:
            self.child = child

    monkeypatch.setattr(mixer.Gtk, "Box", FakeBox)
    monkeypatch.setattr(mixer.Gtk, "CheckButton", FakeCheck)
    strip = SimpleNamespace(
        win=SimpleNamespace(
            _routes={"vm_game.monitor": {"vm_chat"}},
            _routes_known=True,
        ),
        channel=SimpleNamespace(sink="vm_game", group="headset"),
        out_popover=FakePopover(),
        _route_busy=True,
        _route_popover_dirty=False,
        _route_errors={},
        _on_route=lambda *_args: None,
    )

    mixer.Strip._fill_outputs(strip)

    assert strip._route_popover_dirty is True
    assert all(not check.sensitive for check in strip._out_checks.values())


def test_output_popover_disables_controls_until_route_state_is_verified(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mixer = _load_mixer()

    class FakeBox:
        def __init__(self, **_kwargs) -> None:
            self.children = []

        def append(self, child) -> None:
            self.children.append(child)

    class FakeLabel:
        def __init__(self, *, label: str, **_kwargs) -> None:
            self.label = label

        def add_css_class(self, *_args) -> None:
            pass

        def set_wrap(self, *_args) -> None:
            pass

    class FakeCheck:
        def __init__(self, *, label: str) -> None:
            self.label = label
            self.active = False
            self.sensitive = True
            self.tooltip = None

        def set_active(self, active: bool) -> None:
            self.active = active

        def set_sensitive(self, sensitive: bool) -> None:
            self.sensitive = sensitive

        def set_tooltip_text(self, tooltip: str | None) -> None:
            self.tooltip = tooltip

        def connect(self, *_args) -> None:
            pass

    class FakePopover:
        child = None

        def set_child(self, child) -> None:
            self.child = child

    monkeypatch.setattr(mixer.Gtk, "Box", FakeBox)
    monkeypatch.setattr(mixer.Gtk, "Label", FakeLabel)
    monkeypatch.setattr(mixer.Gtk, "CheckButton", FakeCheck)
    strip = SimpleNamespace(
        win=SimpleNamespace(_routes={}, _routes_known=False),
        channel=SimpleNamespace(sink="vm_game", group="headset"),
        out_popover=FakePopover(),
        _route_busy=False,
        _route_errors={},
        _on_route=lambda *_args: None,
    )

    mixer.Strip._fill_outputs(strip)

    checks = list(strip._out_checks.values())
    assert checks
    assert all(check.active is False and not check.sensitive for check in checks)
    assert all("verified" in check.tooltip for check in checks)
    assert strip.out_popover.child.children[0].label.startswith("Waiting for live routing")


def test_input_popover_uses_cached_sources_without_pactl_io(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mixer = _load_mixer()

    class FakeBox:
        def __init__(self, **_kwargs) -> None:
            self.children = []

        def append(self, child) -> None:
            self.children.append(child)

    class FakeLabel:
        def __init__(self, *, label: str, **_kwargs) -> None:
            self.label = label

        def add_css_class(self, *_args) -> None:
            pass

    class FakeCheck:
        def __init__(self, *, label: str) -> None:
            self.label = label
            self.active = False
            self.sensitive = True
            self.tooltip = None

        def set_active(self, active: bool) -> None:
            self.active = active

        def set_sensitive(self, sensitive: bool) -> None:
            self.sensitive = sensitive

        def set_tooltip_text(self, tooltip: str | None) -> None:
            self.tooltip = tooltip

        def connect(self, *_args) -> None:
            pass

    class FakePopover:
        child = None

        def set_child(self, child) -> None:
            self.child = child

    monkeypatch.setattr(mixer.Gtk, "Box", FakeBox)
    monkeypatch.setattr(mixer.Gtk, "Label", FakeLabel)
    monkeypatch.setattr(mixer.Gtk, "CheckButton", FakeCheck)
    monkeypatch.setattr(mixer.mic_routes, "read", lambda: {})
    monkeypatch.setattr(
        mixer.pactl,
        "sources",
        lambda: (_ for _ in ()).throw(AssertionError("GTK callback spawned Pactl")),
    )
    strip = SimpleNamespace(
        win=SimpleNamespace(
            _routes={"sm7b_mono": {"mic_b1"}},
            _sources_present={"sm7b_mono", "astro_mic_48k"},
            _sources_known=True,
        ),
        channel=SimpleNamespace(sink="mic_b1"),
        in_popover=FakePopover(),
        _route_busy=False,
        _on_carla_in=lambda *_args: None,
        _on_mic_in=lambda *_args: None,
    )

    mixer.Strip._fill_inputs(strip)

    checks = {
        check.label: check
        for check in strip.in_popover.child.children
        if hasattr(check, "sensitive")
    }
    assert checks["SM7B (raw)"].sensitive is True
    assert checks["A50 boom mic"].sensitive is True
    assert checks["LifeCam"].sensitive is False
    assert checks["LifeCam"].tooltip == "Not plugged in"


def test_input_popover_disables_carla_and_present_sources_during_route_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mixer = _load_mixer()

    class FakeBox:
        def __init__(self, **_kwargs) -> None:
            self.children = []

        def append(self, child) -> None:
            self.children.append(child)

    class FakeLabel:
        def __init__(self, *, label: str, **_kwargs) -> None:
            self.label = label

        def add_css_class(self, *_args) -> None:
            pass

        def set_wrap(self, *_args) -> None:
            pass

    class FakeCheck:
        def __init__(self, *, label: str) -> None:
            self.label = label
            self.active = False
            self.sensitive = True
            self.tooltip = None

        def set_active(self, active: bool) -> None:
            self.active = active

        def set_sensitive(self, sensitive: bool) -> None:
            self.sensitive = sensitive

        def set_tooltip_text(self, tooltip: str | None) -> None:
            self.tooltip = tooltip

        def connect(self, *_args) -> None:
            pass

    class FakePopover:
        child = None

        def set_child(self, child) -> None:
            self.child = child

    monkeypatch.setattr(mixer.Gtk, "Box", FakeBox)
    monkeypatch.setattr(mixer.Gtk, "Label", FakeLabel)
    monkeypatch.setattr(mixer.Gtk, "CheckButton", FakeCheck)
    monkeypatch.setattr(mixer.mic_routes, "read", lambda: {})
    strip = SimpleNamespace(
        win=SimpleNamespace(
            _routes={"sm7b_mono": {"mic_b1"}},
            _sources_present={"sm7b_mono", "astro_mic_48k"},
            _sources_known=True,
        ),
        channel=SimpleNamespace(sink="mic_b1"),
        in_popover=FakePopover(),
        _route_busy=True,
        _route_popover_dirty=False,
        _on_carla_in=lambda *_args: None,
        _on_mic_in=lambda *_args: None,
    )

    mixer.Strip._fill_inputs(strip)

    checks = {
        check.label: check
        for check in strip.in_popover.child.children
        if hasattr(check, "sensitive")
    }
    assert checks["SM7B via Carla"].sensitive is False
    assert "still running" in checks["SM7B via Carla"].tooltip
    assert checks["SM7B (raw)"].sensitive is False
    assert checks["A50 boom mic"].sensitive is False
    assert "still running" in checks["SM7B (raw)"].tooltip
    assert strip._route_popover_dirty is True


def test_route_settled_keeps_checkbox_state_when_module_read_is_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mixer = _load_mixer()

    class FakeCheck:
        def __init__(self) -> None:
            self.active = True
            self.tooltip = "leave unchanged"

        def set_active(self, active: bool) -> None:
            self.active = active

        def set_tooltip_text(self, tooltip: str | None) -> None:
            self.tooltip = tooltip

    class FakeCollector:
        def refresh_now(self) -> None:
            pass

    monkeypatch.setattr(mixer.pactl, "modules", lambda **_kwargs: [])

    class InlineThread:
        def __init__(self, *, target, **_kwargs) -> None:
            self.target = target

        def start(self) -> None:
            self.target()

    monkeypatch.setattr(mixer.threading, "Thread", InlineThread)
    monkeypatch.setattr(mixer.GLib, "idle_add", lambda callback, *args: callback(*args))
    check = FakeCheck()
    strip = SimpleNamespace(
        win=SimpleNamespace(collector=FakeCollector()),
        channel=SimpleNamespace(sink="vm_game"),
        _out_checks={"vm_chat": check},
        _routing=False,
        _route_busy=False,
        _route_errors={},
        _route_generation=0,
    )
    strip._apply_live_routes = lambda *args: mixer.Strip._apply_live_routes(strip, *args)
    strip._apply_route_snapshot = lambda *args: mixer.Strip._apply_route_snapshot(strip, *args)

    mixer.Strip._route_settled(strip)

    assert check.active is True
    assert check.tooltip == "leave unchanged"
    assert strip._routing is False


def test_input_route_launch_failure_reverts_checkbox(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mixer = _load_mixer()
    from hearth.errors import CommandError

    class FakeCheck:
        active = True
        tooltip = None

        def get_active(self) -> bool:
            return self.active

        def set_active(self, active: bool) -> None:
            self.active = active

        def set_tooltip_text(self, tooltip: str | None) -> None:
            self.tooltip = tooltip

        def get_sensitive(self) -> bool:
            return self.sensitive

        def set_sensitive(self, sensitive: bool) -> None:
            self.sensitive = sensitive

    def fail_to_load(*_args, **_kwargs):
        raise CommandError(["pactl", "load-module"], stderr="not available")

    monkeypatch.setattr(mixer.pactl, "load_loopback", fail_to_load)

    class FakeCollector:
        def refresh_now(self) -> None:
            pass

    class InlineThread:
        def __init__(self, *, target, **_kwargs) -> None:
            self.target = target

        def start(self) -> None:
            self.target()

    monkeypatch.setattr(mixer.threading, "Thread", InlineThread)
    monkeypatch.setattr(mixer.GLib, "idle_add", lambda callback, *args: callback(*args))
    check = FakeCheck()
    check.sensitive = True
    strip = SimpleNamespace(
        _routing=False,
        _route_busy=False,
        _route_errors={},
        _route_prior_sensitivity=[],
        _route_generation=0,
        _route_popover_dirty=False,
        _refresh_visible_route_popovers=lambda **_kwargs: None,
        _out_checks={},
        channel=SimpleNamespace(sink="mic_b1"),
        win=SimpleNamespace(cfg={"latency_msec": 12}, collector=FakeCollector()),
    )
    strip._start_route_job = lambda *args, **kwargs: mixer.Strip._start_route_job(
        strip, *args, **kwargs
    )
    strip._finish_route_job = lambda *args: mixer.Strip._finish_route_job(strip, *args)

    mixer.Strip._on_mic_in(strip, check, "external_mic")

    assert check.active is False
    assert check.tooltip is not None
    assert "Could not route external_mic" in check.tooltip
    assert strip._routing is False


def test_route_job_runs_off_caller_thread_and_disables_controls_until_reconcile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mixer = _load_mixer()
    caller = threading.get_ident()
    operation_thread: list[int] = []
    finished = threading.Event()
    callback_ready = threading.Event()
    queued: list[tuple[object, tuple[object, ...]]] = []

    class FakeCheck:
        def __init__(self, active: bool) -> None:
            self.active = active
            self.tooltip = None
            self.sensitive = True

        def get_active(self) -> bool:
            return self.active

        def set_active(self, active: bool) -> None:
            self.active = active

        def set_tooltip_text(self, tooltip: str | None) -> None:
            self.tooltip = tooltip

        def get_sensitive(self) -> bool:
            return self.sensitive

        def set_sensitive(self, sensitive: bool) -> None:
            self.sensitive = sensitive

    class FakeCollector:
        def refresh_now(self) -> None:
            pass

    modules = [
        mixer.pactl.Module(
            index=55,
            name="module-loopback",
            args="source=vm_game.monitor sink=vm_chat",
        )
    ]
    query_threads: list[int] = []

    def list_modules(**_kwargs):
        query_threads.append(threading.get_ident())
        return modules

    monkeypatch.setattr(mixer.pactl, "modules", list_modules)

    def queue_idle(callback, *args):
        queued.append((callback, args))
        callback_ready.set()
        return 1

    monkeypatch.setattr(mixer.GLib, "idle_add", queue_idle)
    check = FakeCheck(True)
    input_check = FakeCheck(True)
    # The raw source is already unavailable before this unrelated route job;
    # finishing the job must not blindly re-enable it.
    input_check.sensitive = False
    carla_check = FakeCheck(True)
    strip = SimpleNamespace(
        _routing=False,
        _route_busy=False,
        _route_errors={},
        _route_prior_sensitivity=[],
        _route_generation=0,
        _route_popover_dirty=False,
        _refresh_visible_route_popovers=lambda **_kwargs: None,
        _out_checks={"vm_chat": check},
        _in_checks={"sm7b_mono": input_check},
        _carla_check=carla_check,
        channel=SimpleNamespace(sink="vm_game"),
        win=SimpleNamespace(collector=FakeCollector()),
    )
    strip._finish_route_job = lambda *args: mixer.Strip._finish_route_job(strip, *args)
    strip._apply_live_routes = lambda *args: mixer.Strip._apply_live_routes(strip, *args)
    strip._route_settled = lambda: mixer.Strip._route_settled(strip)

    def operation() -> None:
        operation_thread.append(threading.get_ident())
        finished.set()

    mixer.Strip._start_route_job(strip, operation, check, True, route_sink="vm_chat")

    assert not check.sensitive
    assert not input_check.sensitive
    assert not carla_check.sensitive
    assert finished.wait(timeout=2)
    assert callback_ready.wait(timeout=2)
    assert operation_thread[0] != caller
    assert query_threads and query_threads[0] != caller
    assert check.sensitive is False

    callback, args = queued[0]
    callback(*args)

    assert strip._route_busy is False
    assert check.sensitive is True
    assert input_check.sensitive is False
    assert carla_check.sensitive is True
    assert check.active is True


def test_stale_route_query_cannot_overwrite_a_newer_snapshot() -> None:
    mixer = _load_mixer()

    class FakeCheck:
        def __init__(self) -> None:
            self.active = False
            self.tooltip = None

        def set_active(self, active: bool) -> None:
            self.active = active

        def set_tooltip_text(self, tooltip: str | None) -> None:
            self.tooltip = tooltip

    strip = SimpleNamespace(
        _route_generation=8,
        _route_busy=False,
        _routing=False,
        _route_errors={},
        _out_checks={"vm_chat": FakeCheck(), "vm_music": FakeCheck()},
    )
    strip._apply_live_routes = lambda *args: mixer.Strip._apply_live_routes(strip, *args)

    # Generation 7 completed late; generation 8 is the newest live graph.
    mixer.Strip._apply_route_snapshot(strip, 7, {"vm_chat"})
    assert strip._out_checks["vm_chat"].active is False

    mixer.Strip._apply_route_snapshot(strip, 8, {"vm_music"})
    assert strip._out_checks["vm_chat"].active is False
    assert strip._out_checks["vm_music"].active is True


def test_other_input_route_toggles_are_rejected_while_a_route_job_is_busy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mixer = _load_mixer()
    writes: list[str] = []
    monkeypatch.setattr(
        mixer.mic_routes,
        "write",
        lambda *_args, **_kwargs: writes.append("raw") or True,
    )
    monkeypatch.setattr(
        mixer.mic_routes,
        "set_carla",
        lambda *_args, **_kwargs: writes.append("carla") or True,
    )

    class FakeCheck:
        def __init__(self) -> None:
            self.active = True
            self.tooltip = None

        def get_active(self) -> bool:
            return self.active

        def set_active(self, active: bool) -> None:
            self.active = active

        def set_tooltip_text(self, tooltip: str | None) -> None:
            self.tooltip = tooltip

    strip = SimpleNamespace(_routing=False, _route_busy=True)
    raw = FakeCheck()
    carla = FakeCheck()

    mixer.Strip._on_mic_in(strip, raw, "sm7b_mono")
    mixer.Strip._on_carla_in(strip, carla)

    assert raw.active is False
    assert carla.active is False
    assert "still running" in raw.tooltip
    assert "still running" in carla.tooltip
    assert writes == []
    assert strip._routing is False


def test_volume_callback_recovers_if_volume_worker_cannot_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mixer = _load_mixer()

    class FakeWidget:
        text = ""
        tooltip = None

        def set_text(self, text: str) -> None:
            self.text = text

        def set_tooltip_text(self, tooltip: str | None) -> None:
            self.tooltip = tooltip

    refreshes = []
    fader = FakeWidget()
    value = FakeWidget()
    strip = SimpleNamespace(
        _held_until=0.0,
        win=SimpleNamespace(
            quicken=lambda: None,
            queue_volume=lambda *_args: False,
            collector=SimpleNamespace(refresh_now=lambda: refreshes.append(True)),
        ),
        value=value,
        fader=fader,
        channel=SimpleNamespace(sink="vm_game"),
    )

    mixer.Strip._on_moved(strip, None, 55)

    assert strip._held_until == 0.0
    assert value.text == "55"
    assert "Could not set volume" in fader.tooltip
    assert refreshes == [True]


def test_mute_callback_reverts_optimistic_toggle_when_pactl_cannot_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mixer = _load_mixer()
    monkeypatch.setattr(mixer.pactl, "set_mute", lambda *_args: False)

    class FakeButton:
        active = True
        label = ""
        tooltip = None

        def get_active(self) -> bool:
            return self.active

        def set_active(self, active: bool) -> None:
            self.active = active

        def set_label(self, label: str) -> None:
            self.label = label

        def set_tooltip_text(self, tooltip: str | None) -> None:
            self.tooltip = tooltip

        def update_property(self, _properties, values) -> None:
            self.accessible_label = values[0]

        def handler_block(self, _handler) -> None:
            pass

        def handler_unblock(self, _handler) -> None:
            pass

    refreshes = []
    button = FakeButton()
    strip = SimpleNamespace(
        mute=button,
        _mute_handler=1,
        channel=SimpleNamespace(sink="vm_game", name="Game"),
        win=SimpleNamespace(collector=SimpleNamespace(refresh_now=lambda: refreshes.append(True))),
    )

    mixer.Strip._on_mute(strip, button)

    assert button.active is False
    assert button.label == "MUTE"
    assert button.accessible_label == "Mute Game"
    assert "Could not change mute" in button.tooltip
    assert refreshes == [True]

    # A command that succeeds updates the accessible action along with the
    # visible MUTE/MUTED caption, including the subsequent unmute action.
    monkeypatch.setattr(mixer.pactl, "set_mute", lambda *_args: True)
    button.active = True
    mixer.Strip._on_mute(strip, button)
    assert button.active is True
    assert button.label == "MUTED"
    assert button.accessible_label == "Unmute Game"

    button.active = False
    mixer.Strip._on_mute(strip, button)
    assert button.active is False
    assert button.label == "MUTE"
    assert button.accessible_label == "Mute Game"
    assert refreshes == [True]


def test_app_mute_does_not_paint_success_when_pactl_cannot_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mixer = _load_mixer()
    monkeypatch.setattr(mixer.pactl, "set_stream_mute", lambda *_args: False)

    class FakeButton:
        tooltip = None

        def set_tooltip_text(self, tooltip: str | None) -> None:
            self.tooltip = tooltip

    refreshes = []
    listener = mixer.Listener(name="Firefox", index="42", muted=False)
    button = FakeButton()
    strip = SimpleNamespace(
        win=SimpleNamespace(collector=SimpleNamespace(refresh_now=lambda: refreshes.append(True)))
    )

    mixer.Strip._on_app_mute(strip, button, listener)

    assert listener.muted is False
    assert "Could not change app mute" in button.tooltip
    assert refreshes == [True]


def test_app_move_surfaces_pactl_launch_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mixer = _load_mixer()
    monkeypatch.setattr(mixer.pactl, "move_sink_input", lambda *_args: False)

    class FakeStrip:
        tooltip = None
        channel = SimpleNamespace(sink="vm_chat")

        def set_tooltip_text(self, tooltip: str | None) -> None:
            self.tooltip = tooltip

        def _move_app_to(self, listener_index: str, *, restore_focus: bool = False) -> bool:
            return mixer.Strip._move_app_to(self, listener_index, restore_focus=restore_focus)

    strip = FakeStrip()

    assert mixer.Strip._on_drop(strip, None, "app:42", 0.0, 0.0) is False
    assert "Could not move this app" in strip.tooltip


def test_keyboard_app_move_targets_adjacent_visible_bus_and_preserves_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mixer = _load_mixer()
    Gdk = mixer.Gdk

    class FakeMoveControl:
        def __init__(self, focused: bool) -> None:
            self.focused = focused

        def has_focus(self) -> bool:
            return self.focused

    class FakeTarget:
        def __init__(self, sink: str) -> None:
            self.channel = SimpleNamespace(sink=sink)
            self.moves: list[tuple[str, bool]] = []

        def _move_app_to(self, index: str, *, restore_focus: bool = False) -> bool:
            self.moves.append((index, restore_focus))
            return True

    game = FakeTarget("vm_game")
    chat = FakeTarget("vm_chat")
    music = FakeTarget("vm_music")
    window = SimpleNamespace(
        order=["vm_game", "vm_chat", "vm_music", "laptop_audio"],
        strips={"vm_game": game, "vm_chat": chat, "vm_music": music},
        _pending_listener_focus=None,
    )
    move_control = FakeMoveControl(focused=True)
    source = SimpleNamespace(
        channel=game.channel,
        win=window,
        _listener_move_controls={"42": move_control},
    )

    assert mixer.Strip._on_listener_name_key_pressed(
        source, None, Gdk.KEY_Right, 0, Gdk.ModifierType(0), "42"
    )
    assert chat.moves == [("42", True)]

    # A bubbling Left/Right event from the nested volume scale must not move
    # the stream. The action is only active while the row itself owns focus.
    move_control.focused = False
    assert not mixer.Strip._on_listener_name_key_pressed(
        source, None, Gdk.KEY_Right, 0, Gdk.ModifierType(0), "42"
    )
    assert chat.moves == [("42", True)]
    move_control.focused = True

    assert not mixer.Strip._on_listener_name_key_pressed(
        source, None, Gdk.KEY_Escape, 0, Gdk.ModifierType(0), "42"
    )
    assert mixer.Strip._on_listener_name_key_pressed(
        source, None, Gdk.KEY_Left, 0, Gdk.ModifierType(0), "42"
    )
    assert chat.moves == [("42", True)]
    assert not mixer.Strip._on_listener_name_key_pressed(
        SimpleNamespace(
            channel=SimpleNamespace(sink="laptop_audio"),
            win=window,
            _listener_move_controls={"42": FakeMoveControl(focused=True)},
        ),
        None,
        Gdk.KEY_Right,
        0,
        Gdk.ModifierType(0),
        "42",
    )


def test_setup_save_queues_file_write_outside_gtk_callback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    setup = _load_setup()
    writes: list[tuple[Path, dict[str, str]]] = []
    workers = []

    class FakeButton:
        label = "Save"
        tooltip = ""
        sensitive = True

        def set_label(self, label: str) -> None:
            self.label = label

        def set_tooltip_text(self, tooltip: str | None) -> None:
            self.tooltip = tooltip

        def set_sensitive(self, sensitive: bool) -> None:
            self.sensitive = sensitive

    class FakeToggle:
        sensitive = True

        def get_active(self) -> bool:
            return True

        def set_sensitive(self, sensitive: bool) -> None:
            self.sensitive = sensitive

    class FakeSpin:
        sensitive = True

        def get_value(self) -> float:
            return 18.0

        def set_sensitive(self, sensitive: bool) -> None:
            self.sensitive = sensitive

    class CapturedThread:
        def __init__(self, *, target, args=(), kwargs=None, **_unused) -> None:
            self.target = target
            self.args = args
            self.kwargs = kwargs or {}

        def start(self) -> None:
            workers.append(self)

    core = SimpleNamespace(
        MXCONF=tmp_path / "roaring_mixer.conf",
        ASTRO_GAME="game",
        ASTRO_CHAT="chat",
        write_conf_keys=lambda path, updates: writes.append((path, dict(updates))) or True,
    )
    window = SimpleNamespace(
        _core=core,
        _device_save_pending=False,
        astro_chat=FakeToggle(),
        astro_game=FakeToggle(),
        latency=FakeSpin(),
        save_devices=FakeButton(),
    )
    window._save_devices_worker = lambda updates: setup.SetupWindow._save_devices_worker(
        window, updates
    )
    window._finish_save_devices = lambda *args: setup.SetupWindow._finish_save_devices(
        window, *args
    )
    window._devices_settled = lambda: False
    monkeypatch.setattr(setup.threading, "Thread", CapturedThread)
    monkeypatch.setattr(setup.GLib, "idle_add", lambda callback, *args: callback(*args))
    monkeypatch.setattr(setup.GLib, "timeout_add_seconds", lambda *_args: 1)
    monkeypatch.setattr(setup.services_mod, "restart", lambda *_args: True)

    setup.SetupWindow._on_save_devices(window, window.save_devices)

    # The click handler returned without touching the locked config file.
    assert writes == []
    assert len(workers) == 1
    assert window._device_save_pending is True
    assert window.save_devices.label == "Saving…"
    assert not window.save_devices.sensitive
    assert not window.astro_game.sensitive
    assert not window.astro_chat.sensitive
    assert not window.latency.sensitive

    # Simulate the worker after the signal handler has returned.
    worker = workers[0]
    worker.target(*worker.args, **worker.kwargs)

    assert writes == [
        (
            core.MXCONF,
            {"ASTRO_TARGET": "game", "LATENCY_MSEC": "18"},
        )
    ]
    assert window.save_devices.label == "Saved"
    assert window._device_save_pending is False
    assert window.save_devices.sensitive
    assert window.astro_game.sensitive
    assert window.astro_chat.sensitive
    assert window.latency.sensitive


def test_setup_save_recovers_if_worker_thread_cannot_start(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    setup = _load_setup()

    class FakeButton:
        label = "Save"
        tooltip = ""
        sensitive = True

        def set_label(self, label: str) -> None:
            self.label = label

        def set_tooltip_text(self, tooltip: str | None) -> None:
            self.tooltip = tooltip

        def set_sensitive(self, sensitive: bool) -> None:
            self.sensitive = sensitive

    class FakeToggle:
        sensitive = True

        def get_active(self) -> bool:
            return False

        def set_sensitive(self, sensitive: bool) -> None:
            self.sensitive = sensitive

    class FakeSpin:
        sensitive = True

        def get_value(self) -> float:
            return 12.0

        def set_sensitive(self, sensitive: bool) -> None:
            self.sensitive = sensitive

    class ThreadThatCannotStart:
        def __init__(self, **_kwargs) -> None:
            pass

        def start(self) -> None:
            raise RuntimeError("thread limit reached")

    core = SimpleNamespace(
        MXCONF=tmp_path / "unused.conf",
        ASTRO_GAME="game",
        ASTRO_CHAT="chat",
        write_conf_keys=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("worker failed before the config write")
        ),
    )
    window = SimpleNamespace(
        _core=core,
        _device_save_pending=False,
        astro_chat=FakeToggle(),
        astro_game=FakeToggle(),
        latency=FakeSpin(),
        save_devices=FakeButton(),
    )
    window._devices_settled = lambda: False
    window._finish_save_devices = lambda *args: setup.SetupWindow._finish_save_devices(
        window, *args
    )
    window._save_devices_worker = lambda _updates: (_ for _ in ()).throw(
        AssertionError("worker must not run when thread start fails")
    )
    monkeypatch.setattr(setup.threading, "Thread", ThreadThatCannotStart)
    monkeypatch.setattr(setup.GLib, "timeout_add_seconds", lambda *_args: 1)

    setup.SetupWindow._on_save_devices(window, window.save_devices)

    assert window.save_devices.label == "Save failed"
    assert "could not start config save" in window.save_devices.tooltip.lower()
    assert window._device_save_pending is False
    assert window.save_devices.sensitive
    assert window.astro_chat.sensitive
    assert window.astro_game.sensitive
    assert window.latency.sensitive


def test_setup_save_reports_saved_config_when_restart_cannot_start(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    setup = _load_setup()
    writes: list[tuple[Path, dict[str, str]]] = []
    restart_calls: list[tuple[str, ...]] = []
    timers: list[tuple[int, object]] = []

    class FakeButton:
        label = "Save"
        tooltip = "Write the config and restart routing"
        sensitive = True

        def set_label(self, label: str) -> None:
            self.label = label

        def set_tooltip_text(self, tooltip: str | None) -> None:
            self.tooltip = tooltip

        def set_sensitive(self, sensitive: bool) -> None:
            self.sensitive = sensitive

    class FakeToggle:
        sensitive = True

        def get_active(self) -> bool:
            return True

        def set_sensitive(self, sensitive: bool) -> None:
            self.sensitive = sensitive

    class FakeSpin:
        sensitive = True

        def get_value(self) -> float:
            return 18.0

        def set_sensitive(self, sensitive: bool) -> None:
            self.sensitive = sensitive

    core = SimpleNamespace(
        MXCONF=tmp_path / "roaring_mixer.conf",
        ASTRO_GAME="alsa_output.usb-Astro.stereo-game",
        ASTRO_CHAT="alsa_output.usb-Astro.stereo-chat",
        write_conf_keys=lambda path, updates: writes.append((path, dict(updates))) or True,
    )
    window = SimpleNamespace(
        _core=core,
        astro_chat=FakeToggle(),
        astro_game=FakeToggle(),
        latency=FakeSpin(),
        save_devices=FakeButton(),
        _device_save_pending=False,
    )
    window._devices_settled = lambda: False
    window._finish_save_devices = lambda *args: setup.SetupWindow._finish_save_devices(
        window, *args
    )
    window._save_devices_worker = lambda updates: setup.SetupWindow._save_devices_worker(
        window, updates
    )

    class InlineThread:
        def __init__(self, *, target, args=(), kwargs=None, **_unused) -> None:
            self.target = target
            self.args = args
            self.kwargs = kwargs or {}

        def start(self) -> None:
            self.target(*self.args, **self.kwargs)

    monkeypatch.setattr(setup.threading, "Thread", InlineThread)
    monkeypatch.setattr(setup.GLib, "idle_add", lambda callback, *args: callback(*args))

    def restart(*units: str) -> bool:
        restart_calls.append(units)
        return False

    monkeypatch.setattr(setup.services_mod, "restart", restart)
    monkeypatch.setattr(
        setup.GLib,
        "timeout_add_seconds",
        lambda seconds, callback: timers.append((seconds, callback)) or 1,
    )

    setup.SetupWindow._on_save_devices(window, window.save_devices)

    assert writes == [
        (
            core.MXCONF,
            {
                "ASTRO_TARGET": "alsa_output.usb-Astro.stereo-game",
                "LATENCY_MSEC": "18",
            },
        )
    ]
    assert restart_calls == [(setup.ROUTES_UNIT,)]
    assert window.save_devices.label == "Saved*"
    assert "saved" in window.save_devices.tooltip.lower()
    assert "could not be restarted" in window.save_devices.tooltip.lower()
    assert len(timers) == 1


def test_setup_save_does_not_restart_or_claim_success_when_config_write_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    setup = _load_setup()
    restart_calls: list[tuple[str, ...]] = []

    class FakeButton:
        label = "Save"
        tooltip = None
        sensitive = True

        def set_label(self, label: str) -> None:
            self.label = label

        def set_tooltip_text(self, tooltip: str | None) -> None:
            self.tooltip = tooltip

        def set_sensitive(self, sensitive: bool) -> None:
            self.sensitive = sensitive

    class FakeToggle:
        sensitive = True

        def get_active(self) -> bool:
            return False

        def set_sensitive(self, sensitive: bool) -> None:
            self.sensitive = sensitive

    class FakeSpin:
        sensitive = True

        def get_value(self) -> float:
            return 12.0

        def set_sensitive(self, sensitive: bool) -> None:
            self.sensitive = sensitive

    core = SimpleNamespace(
        MXCONF=tmp_path / "missing.conf",
        ASTRO_GAME="game",
        ASTRO_CHAT="chat",
        write_conf_keys=lambda *_args, **_kwargs: False,
    )
    window = SimpleNamespace(
        _core=core,
        astro_chat=FakeToggle(),
        astro_game=FakeToggle(),
        latency=FakeSpin(),
        save_devices=FakeButton(),
        _device_save_pending=False,
    )
    window._devices_settled = lambda: False
    window._finish_save_devices = lambda *args: setup.SetupWindow._finish_save_devices(
        window, *args
    )
    window._save_devices_worker = lambda updates: setup.SetupWindow._save_devices_worker(
        window, updates
    )

    class InlineThread:
        def __init__(self, *, target, args=(), kwargs=None, **_unused) -> None:
            self.target = target
            self.args = args
            self.kwargs = kwargs or {}

        def start(self) -> None:
            self.target(*self.args, **self.kwargs)

    monkeypatch.setattr(setup.threading, "Thread", InlineThread)
    monkeypatch.setattr(setup.GLib, "idle_add", lambda callback, *args: callback(*args))
    monkeypatch.setattr(
        setup.services_mod,
        "restart",
        lambda *units: restart_calls.append(units) or True,
    )
    monkeypatch.setattr(setup.GLib, "timeout_add_seconds", lambda *_args: 1)

    setup.SetupWindow._on_save_devices(window, window.save_devices)

    assert window.save_devices.label == "Save failed"
    assert "could not save" in window.save_devices.tooltip.lower()
    assert restart_calls == []


def test_moonlight_save_failure_reverts_radio_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    setup = _load_setup()
    monkeypatch.setattr(setup.GLib, "idle_add", lambda callback, *args: callback(*args))

    class FakeCheck:
        def __init__(self, active: bool) -> None:
            self.active = active
            self.sensitive = False

        def set_active(self, active: bool) -> None:
            self.active = active

        def set_sensitive(self, sensitive: bool) -> None:
            self.sensitive = sensitive

    class FakeNote:
        text = ""

        def set_text(self, text: str) -> None:
            self.text = text

    stream = FakeCheck(True)
    chat = FakeCheck(False)
    note = FakeNote()
    strip = SimpleNamespace(
        _core=SimpleNamespace(set_moonlight_mic=lambda _source: (False, False)),
        _moonlight_updating=True,
        ml_stream=stream,
        ml_chat=chat,
        ml_note=note,
        _refresh_flow=lambda: None,
    )
    strip._moonlight_done = lambda *args: setup.SetupWindow._moonlight_done(strip, *args)

    setup.SetupWindow._set_moonlight(strip, "b1_mic", "b2_mic")

    assert stream.active is False
    assert chat.active is True
    assert stream.sensitive is True
    assert chat.sensitive is True
    assert strip._moonlight_updating is False
    assert "Could not save Moonlight mic source" in note.text
    assert "previous setting was preserved" in note.text


def test_moonlight_success_keeps_selection_and_reports_restart_requested(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    setup = _load_setup()
    monkeypatch.setattr(setup.GLib, "idle_add", lambda callback, *args: callback(*args))

    class FakeCheck:
        def __init__(self, active: bool) -> None:
            self.active = active
            self.sensitive = False

        def set_active(self, active: bool) -> None:
            self.active = active

        def set_sensitive(self, sensitive: bool) -> None:
            self.sensitive = sensitive

    class FakeNote:
        text = ""

        def set_text(self, text: str) -> None:
            self.text = text

    stream = FakeCheck(False)
    chat = FakeCheck(True)
    note = FakeNote()
    strip = SimpleNamespace(
        _core=SimpleNamespace(set_moonlight_mic=lambda source: (source == "b2_mic", True)),
        _moonlight_updating=True,
        ml_stream=stream,
        ml_chat=chat,
        ml_note=note,
        _refresh_flow=lambda: None,
    )
    strip._moonlight_done = lambda *args: setup.SetupWindow._moonlight_done(strip, *args)

    setup.SetupWindow._set_moonlight(strip, "b2_mic", "b1_mic")

    assert stream.active is False
    assert chat.active is True
    assert stream.sensitive is True
    assert chat.sensitive is True
    assert strip._moonlight_updating is False
    assert note.text == "Saved; routing restart requested"


def test_moonlight_queue_failure_keeps_saved_selection_and_explains_manual_restart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    setup = _load_setup()
    monkeypatch.setattr(setup.GLib, "idle_add", lambda callback, *args: callback(*args))

    class FakeCheck:
        def __init__(self, active: bool) -> None:
            self.active = active
            self.sensitive = False

        def set_active(self, active: bool) -> None:
            self.active = active

        def set_sensitive(self, sensitive: bool) -> None:
            self.sensitive = sensitive

    class FakeNote:
        text = ""

        def set_text(self, text: str) -> None:
            self.text = text

    # The user just selected Stream; the config was saved, but the restart
    # thread couldn't be started, so this choice must not roll back.
    stream = FakeCheck(True)
    chat = FakeCheck(False)
    note = FakeNote()
    strip = SimpleNamespace(
        _core=SimpleNamespace(set_moonlight_mic=lambda _source: (True, False)),
        _moonlight_updating=True,
        ml_stream=stream,
        ml_chat=chat,
        ml_note=note,
        _refresh_flow=lambda: None,
    )
    strip._moonlight_done = lambda *args: setup.SetupWindow._moonlight_done(strip, *args)

    setup.SetupWindow._set_moonlight(strip, "b1_mic", "b2_mic")

    assert stream.active is True
    assert chat.active is False
    assert stream.sensitive is True
    assert chat.sensitive is True
    assert strip._moonlight_updating is False
    assert "Saved, but could not queue" in note.text
    assert "manually" in note.text


def test_setup_scan_can_retry_after_worker_thread_creation_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    setup = _load_setup()

    class ThreadThatCannotStart:
        def __init__(self, **_kwargs) -> None:
            pass

        def start(self) -> None:
            raise RuntimeError("thread limit reached")

    monkeypatch.setattr(setup.threading, "Thread", ThreadThatCannotStart)
    window = SimpleNamespace(_scanning=False, _scan=lambda: None)

    assert setup.SetupWindow._refresh_once(window) is False
    assert window._scanning is False


def test_unmute_ipc_reports_worker_thread_creation_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mixer = _load_mixer()

    class ThreadThatCannotStart:
        def __init__(self, **_kwargs) -> None:
            pass

        def start(self) -> None:
            raise RuntimeError("thread limit reached")

    monkeypatch.setattr(mixer.threading, "Thread", ThreadThatCannotStart)

    reply = mixer.MixerApp._ctl_unmute(SimpleNamespace())

    assert reply.startswith("could not start unmute operation")
    assert "thread limit reached" in reply


def test_dump_writes_atomic_private_summary_snapshot(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    mixer = _load_mixer()
    from hearth import paths, status

    dumps = tmp_path / "dumps"
    dumps.mkdir()
    target = dumps / "summary.txt"
    target.write_text("old summary\n", encoding="utf-8")
    target.chmod(0o644)
    monkeypatch.setattr(paths, "dumps_dir", lambda: dumps)
    monkeypatch.setattr(status, "text", lambda: "current rig summary")

    class InlineThread:
        def __init__(self, *, target, **_kwargs) -> None:
            self.target = target

        def start(self) -> None:
            self.target()

    monkeypatch.setattr(mixer.threading, "Thread", InlineThread)

    reply = mixer.MixerApp._ctl_dump(SimpleNamespace())

    assert reply.endswith(str(target))
    assert target.read_text(encoding="utf-8") == "current rig summary\n"
    assert target.stat().st_mode & 0o777 == 0o600
    assert dumps.stat().st_mode & 0o777 == 0o700
    assert list(dumps.glob("summary.txt.tmp.*")) == []


def test_dump_ipc_does_not_claim_success_when_worker_cannot_start(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    mixer = _load_mixer()
    from hearth import paths

    monkeypatch.setattr(paths, "dumps_dir", lambda: tmp_path / "dumps")

    class ThreadThatCannotStart:
        def __init__(self, **_kwargs) -> None:
            pass

        def start(self) -> None:
            raise RuntimeError("thread limit reached")

    monkeypatch.setattr(mixer.threading, "Thread", ThreadThatCannotStart)

    reply = mixer.MixerApp._ctl_dump(SimpleNamespace())

    assert reply.startswith("dump failed to start")
    assert "thread limit reached" in reply
    assert not (tmp_path / "dumps").exists()


def test_raw_mic_route_config_write_is_deferred_to_route_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mixer = _load_mixer()
    writes: list[dict[str, str]] = []
    captured: list[tuple[object, object, bool, dict[str, object]]] = []

    monkeypatch.setattr(mixer.mic_routes, "read", lambda: {"B1_ROUTE": "sm7b"})
    monkeypatch.setattr(
        mixer.mic_routes,
        "raw_edits",
        lambda _conf, sink, source, wanted: {"B1_ROUTE": f"{sink}:{source}:{wanted}"},
    )
    monkeypatch.setattr(
        mixer.mic_routes,
        "write",
        lambda updates: writes.append(updates) or True,
    )

    class FakeCheck:
        active = True

        def get_active(self) -> bool:
            return self.active

        def set_active(self, active: bool) -> None:
            self.active = active

        def set_tooltip_text(self, _text: str | None) -> None:
            pass

    check = FakeCheck()

    def start_route_job(operation, widget, wanted, **kwargs) -> None:
        captured.append((operation, widget, wanted, kwargs))

    strip = SimpleNamespace(
        _routing=False,
        _route_busy=False,
        channel=SimpleNamespace(sink="mic_b1"),
        _start_route_job=start_route_job,
    )

    mixer.Strip._on_mic_in(strip, check, "sm7b_mono")

    assert writes == []
    assert len(captured) == 1
    operation, widget, wanted, kwargs = captured[0]
    assert widget is check
    assert wanted is True
    assert kwargs["error_prefix"] == "Could not save microphone routing"

    # The locked/atomic write occurs only when the route-job worker invokes it.
    operation()
    assert writes == [{"B1_ROUTE": "mic_b1:sm7b_mono:True"}]


def test_route_popovers_refresh_only_for_changed_inventory_and_defer_while_busy() -> None:
    mixer = _load_mixer()
    events: list[str] = []

    class FakePopover:
        def __init__(self, visible: bool) -> None:
            self.visible = visible

        def get_visible(self) -> bool:
            return self.visible

    strip = SimpleNamespace(
        _route_busy=False,
        _route_popover_dirty=False,
        out_popover=FakePopover(True),
        in_button=object(),
        in_popover=FakePopover(True),
        _fill_outputs=lambda: events.append("out"),
        _fill_inputs=lambda: events.append("in"),
    )

    # Identical consecutive collector snapshots must not rebuild a popover
    # out from under a user's click every 500 ms.
    mixer.Strip._refresh_visible_route_popovers(strip, routes_changed=False, sources_changed=False)
    assert events == []

    mixer.Strip._refresh_visible_route_popovers(strip, routes_changed=True, sources_changed=False)
    assert events == ["out", "in"]

    # If the route worker is mutating the graph, defer any rebuild until its
    # completion callback has released the controls.
    strip._route_busy = True
    mixer.Strip._refresh_visible_route_popovers(strip, routes_changed=True, sources_changed=True)
    assert events == ["out", "in"]
    assert strip._route_popover_dirty is True

    strip._route_busy = False
    mixer.Strip._refresh_visible_route_popovers(
        strip,
        routes_changed=strip._route_popover_dirty,
        sources_changed=strip._route_popover_dirty,
    )
    assert events == ["out", "in", "out", "in"]
    assert strip._route_popover_dirty is False


def test_layout_gestures_do_not_persist_user_preferences() -> None:
    module = _load_func_check()

    class DummyWindow:
        def __init__(self) -> None:
            self.save_calls = 0

        def _save(self) -> None:
            self.save_calls += 1

    window = DummyWindow()
    module.disable_settings_persistence(window)
    window._save()

    assert window.save_calls == 0


def test_led_updates_run_off_gtk_thread_and_coalesce_latest_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mixer = _load_mixer()
    caller = threading.get_ident()
    port_lookup_started = threading.Event()
    allow_port_lookup_to_finish = threading.Event()
    worker_finished = threading.Event()
    worker_ids: list[int] = []
    sent: list[tuple[str, bool]] = []

    def find_port() -> str:
        worker_ids.append(threading.get_ident())
        port_lookup_started.set()
        assert allow_port_lookup_to_finish.wait(timeout=3)
        return "hw:1,0,0"

    def set_mute(_port: str, sink: str, muted: bool, _channel: int) -> bool:
        worker_ids.append(threading.get_ident())
        sent.append((sink, muted))
        return True

    monkeypatch.setattr(mixer.leds, "find_port", find_port)
    monkeypatch.setattr(mixer.leds, "set_sink_mute", set_mute)
    window = SimpleNamespace(
        _led_port=None,
        _led_lock=threading.Lock(),
        _led_pending={},
        _led_worker_active=False,
        cfg={},
    )
    real_drain = mixer.MixerWindow._drain_led_updates

    def drain_and_signal() -> None:
        try:
            real_drain(window)
        finally:
            worker_finished.set()

    window._drain_led_updates = drain_and_signal
    window._push_led = lambda sink, muted: mixer.MixerWindow._push_led(window, sink, muted)

    window._push_led("vm_game", True)
    assert port_lookup_started.wait(timeout=2)
    # The worker is blocked on port discovery. These two updates should be
    # coalesced into the next batch, with the most recent game state winning.
    window._push_led("vm_game", False)
    window._push_led("vm_chat", True)
    allow_port_lookup_to_finish.set()

    assert worker_finished.wait(timeout=3)
    assert sent == [("vm_game", True), ("vm_game", False), ("vm_chat", True)]
    assert worker_ids and all(worker_id != caller for worker_id in worker_ids)
    assert window._led_worker_active is False


def test_led_worker_invalidates_disappeared_port_and_reprobes_next_update(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mixer = _load_mixer()
    port_lookups: list[bool] = []
    writes: list[tuple[str, bool]] = []
    completions: list[threading.Event] = []

    def find_port() -> str | None:
        port_lookups.append(True)
        return None

    def failed_write(_port: str, sink: str, muted: bool, _channel: int) -> bool:
        writes.append((sink, muted))
        return False

    monkeypatch.setattr(mixer.leds, "find_port", find_port)
    monkeypatch.setattr(mixer.leds, "set_sink_mute", failed_write)
    window = SimpleNamespace(
        _led_port="hw:6,0,0",  # Cached discovery succeeds before the device disconnects.
        _led_lock=threading.Lock(),
        _led_pending={},
        _led_worker_active=False,
        cfg={},
    )
    real_drain = mixer.MixerWindow._drain_led_updates

    def drain_and_signal() -> None:
        completion = completions[-1]
        try:
            real_drain(window)
        finally:
            completion.set()

    window._drain_led_updates = drain_and_signal
    window._push_led = lambda sink, muted: mixer.MixerWindow._push_led(window, sink, muted)

    first = threading.Event()
    completions.append(first)
    window._push_led("vm_game", True)
    assert first.wait(timeout=2)
    assert writes == [("vm_game", True)]
    assert window._led_port is None
    assert window._led_worker_active is False
    assert window._led_pending == {}

    # The next state change must discover again; a missing controller drops
    # the batch and returns to idle instead of retrying a stale port forever.
    second = threading.Event()
    completions.append(second)
    window._push_led("vm_chat", False)
    assert second.wait(timeout=2)
    assert len(port_lookups) == 1
    assert writes == [("vm_game", True)]
    assert window._led_worker_active is False
    assert window._led_pending == {}


def test_led_queue_recovers_if_worker_thread_cannot_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mixer = _load_mixer()

    class ThreadThatCannotStart:
        def __init__(self, **_kwargs) -> None:
            pass

        def start(self) -> None:
            raise RuntimeError("thread limit reached")

    monkeypatch.setattr(mixer.threading, "Thread", ThreadThatCannotStart)
    window = SimpleNamespace(
        _led_port=None,
        _led_lock=threading.Lock(),
        _led_pending={},
        _led_worker_active=False,
        cfg={},
        _drain_led_updates=lambda: None,
    )

    mixer.MixerWindow._push_led(window, "vm_game", True)

    assert window._led_worker_active is False
    assert window._led_pending == {"vm_game": True}


def _fake_remap_window(remap):
    class FakeControl:
        def __init__(self) -> None:
            self.sensitive = True

        def set_sensitive(self, sensitive: bool) -> None:
            self.sensitive = sensitive

    controls = [FakeControl(), FakeControl()]
    messages: list[str] = []
    refreshed: list[bool] = []
    restarts: list[bool] = []
    window = SimpleNamespace(
        _write_pending=False,
        _write_error="",
        _service_state=None,
        _service_query_pending=False,
        _map_usable=True,
        _clash_note="",
        _pads={1: controls[0]},
        _knobs={1: controls[1]},
        _say=messages.append,
        _queue_restart=lambda: restarts.append(True),
    )
    window._service_note = lambda: remap.RemapWindow._service_note(window)
    window._show_status = lambda: remap.RemapWindow._show_status(window)

    def refresh() -> None:
        refreshed.append(True)
        for control in controls:
            control.set_sensitive(not window._write_pending)
        window._show_status()

    window._refresh_map = refresh
    window._finish_write = lambda changed, error="": remap.RemapWindow._finish_write(
        window, changed, error
    )
    return window, controls, messages, refreshed, restarts


def test_lpd8_remap_service_status_query_runs_off_gtk_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from hearth.ui.gtk4 import remap

    workers = []
    calls: list[list[str]] = []
    messages: list[str] = []

    class CapturedThread:
        def __init__(self, *, target, **_kwargs) -> None:
            self.target = target

        def start(self) -> None:
            workers.append(self)

    monkeypatch.setattr(remap.threading, "Thread", CapturedThread)
    monkeypatch.setattr(remap.GLib, "idle_add", lambda callback, *args: callback(*args))
    monkeypatch.setattr(
        remap.services_mod,
        "states",
        lambda units: calls.append(list(units)) or {remap.lpd8map.UNIT: "inactive"},
    )
    window = SimpleNamespace(
        _service_query_pending=False,
        _service_state=None,
        _write_pending=False,
        _write_error="",
        _map_usable=True,
        _clash_note="",
        _say=messages.append,
    )
    window._service_note = lambda: remap.RemapWindow._service_note(window)
    window._show_status = lambda: remap.RemapWindow._show_status(window)
    window._finish_service_state = lambda state: remap.RemapWindow._finish_service_state(
        window, state
    )

    remap.RemapWindow._queue_service_state_refresh(window)

    assert calls == []
    assert window._service_query_pending is True
    assert messages == []

    workers[0].target()

    assert calls == [[remap.lpd8map.UNIT]]
    assert window._service_query_pending is False
    assert window._service_state == "inactive"
    assert messages == ["The LPD8 service is not running — knobs and pads do nothing until it is"]


def test_lpd8_remap_refresh_keeps_controls_busy_without_claiming_map_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from hearth.ui.gtk4 import remap

    class FakeControl:
        def __init__(self) -> None:
            self.sensitive = True
            self.label = ""
            self.tooltip = ""

        def show_job(self, label: str, tooltip: str) -> None:
            self.label = label
            self.tooltip = tooltip

        def set_sensitive(self, sensitive: bool) -> None:
            self.sensitive = sensitive

    monkeypatch.setattr(remap.lpd8map, "read_map", lambda: {"CC_VM_GAME": 0})
    monkeypatch.setattr(remap.lpd8map, "read_pad_map", lambda: {1: 1})
    controls = [FakeControl(), FakeControl()]
    messages: list[str] = []
    query_requests: list[bool] = []
    window = SimpleNamespace(
        _write_pending=True,
        _write_error="",
        _service_state=None,
        _service_query_pending=True,
        _map_usable=False,
        _clash_note="",
        _pads={1: controls[0]},
        _knobs={1: controls[1]},
        _open=None,
        _say=messages.append,
        _queue_service_state_refresh=lambda: query_requests.append(True),
        _paint_live=lambda: None,
        _clashes=lambda *_args: "",
    )
    window._service_note = lambda: remap.RemapWindow._service_note(window)
    window._show_status = lambda: remap.RemapWindow._show_status(window)

    remap.RemapWindow._refresh_map(window)

    assert window._map_usable is True
    assert all(not control.sensitive for control in controls)
    assert messages == ["Saving LPD8 mapping…"]
    assert query_requests == [True]


def test_lpd8_remap_mapping_write_is_deferred_and_queues_restart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from hearth.ui.gtk4 import remap

    workers = []

    class CapturedThread:
        def __init__(self, *, target, **_kwargs) -> None:
            self.target = target

        def start(self) -> None:
            workers.append(self)

    monkeypatch.setattr(remap.threading, "Thread", CapturedThread)
    monkeypatch.setattr(remap.GLib, "idle_add", lambda callback, *args: callback(*args))
    window, controls, messages, refreshed, restarts = _fake_remap_window(remap)
    edits: list[bool] = []

    remap.RemapWindow._write(window, lambda: edits.append(True) or True)

    # The signal handler returns without taking the map file lock or writing it.
    assert edits == []
    assert len(workers) == 1
    assert window._write_pending is True
    assert all(not control.sensitive for control in controls)
    assert messages == ["Saving LPD8 mapping…"]
    assert restarts == []

    workers[0].target()

    assert edits == [True]
    assert window._write_pending is False
    assert all(control.sensitive for control in controls)
    assert refreshed == [True]
    assert restarts == [True]


def test_lpd8_remap_write_error_restores_controls_without_restart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from hearth.ui.gtk4 import remap

    workers = []

    class CapturedThread:
        def __init__(self, *, target, **_kwargs) -> None:
            self.target = target

        def start(self) -> None:
            workers.append(self)

    monkeypatch.setattr(remap.threading, "Thread", CapturedThread)
    monkeypatch.setattr(remap.GLib, "idle_add", lambda callback, *args: callback(*args))
    window, controls, messages, refreshed, restarts = _fake_remap_window(remap)

    def failed_edit() -> bool:
        raise OSError("disk full")

    remap.RemapWindow._write(window, failed_edit)
    assert window._write_pending is True
    workers[0].target()

    assert window._write_pending is False
    assert all(control.sensitive for control in controls)
    assert refreshed == [True]
    assert "Could not save that: disk full" in messages[-1]
    assert restarts == []


def test_lpd8_remap_write_recovers_if_worker_cannot_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from hearth.ui.gtk4 import remap

    class ThreadThatCannotStart:
        def __init__(self, **_kwargs) -> None:
            pass

        def start(self) -> None:
            raise RuntimeError("thread limit reached")

    monkeypatch.setattr(remap.threading, "Thread", ThreadThatCannotStart)
    window, controls, messages, refreshed, restarts = _fake_remap_window(remap)
    edits: list[bool] = []

    remap.RemapWindow._write(window, lambda: edits.append(True) or True)

    assert edits == []
    assert window._write_pending is False
    assert all(control.sensitive for control in controls)
    assert refreshed == [True]
    assert "Could not start LPD8 map save: thread limit reached" in messages[-1]
    assert restarts == []
