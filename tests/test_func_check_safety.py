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
        raise mixer.CommandError(["pactl", "load-module"], stderr="not available")

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
    strip = SimpleNamespace(
        _routing=False,
        _route_busy=False,
        _route_errors={},
        _route_prior_sensitivity=[],
        _route_generation=0,
        _out_checks={"vm_chat": check},
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
    assert finished.wait(timeout=2)
    assert callback_ready.wait(timeout=2)
    assert operation_thread[0] != caller
    assert query_threads and query_threads[0] != caller
    assert check.sensitive is False

    callback, args = queued[0]
    callback(*args)

    assert strip._route_busy is False
    assert check.sensitive is True
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


def test_volume_callback_recovers_from_pactl_spawn_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mixer = _load_mixer()
    monkeypatch.setattr(mixer.pactl, "set_volume", lambda *_args: False)

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

        def handler_block(self, _handler) -> None:
            pass

        def handler_unblock(self, _handler) -> None:
            pass

    refreshes = []
    button = FakeButton()
    strip = SimpleNamespace(
        mute=button,
        _mute_handler=1,
        channel=SimpleNamespace(sink="vm_game"),
        win=SimpleNamespace(collector=SimpleNamespace(refresh_now=lambda: refreshes.append(True))),
    )

    mixer.Strip._on_mute(strip, button)

    assert button.active is False
    assert button.label == "MUTE"
    assert "Could not change mute" in button.tooltip
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

    strip = FakeStrip()

    assert mixer.Strip._on_drop(strip, None, "app:42", 0.0, 0.0) is False
    assert "Could not move this app" in strip.tooltip


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

        def set_label(self, label: str) -> None:
            self.label = label

        def set_tooltip_text(self, tooltip: str | None) -> None:
            self.tooltip = tooltip

    class FakeToggle:
        def get_active(self) -> bool:
            return True

    class FakeSpin:
        def get_value(self) -> float:
            return 18.0

    core = SimpleNamespace(
        MXCONF=tmp_path / "roaring_mixer.conf",
        ASTRO_GAME="alsa_output.usb-Astro.stereo-game",
        ASTRO_CHAT="alsa_output.usb-Astro.stereo-chat",
        write_conf_keys=lambda path, updates: writes.append((path, dict(updates))) or True,
    )
    window = SimpleNamespace(
        _core=core,
        astro_game=FakeToggle(),
        latency=FakeSpin(),
        save_devices=FakeButton(),
    )
    window._devices_settled = lambda: False

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

        def set_label(self, label: str) -> None:
            self.label = label

        def set_tooltip_text(self, tooltip: str | None) -> None:
            self.tooltip = tooltip

    class FakeToggle:
        def get_active(self) -> bool:
            return False

    class FakeSpin:
        def get_value(self) -> float:
            return 12.0

    core = SimpleNamespace(
        MXCONF=tmp_path / "missing.conf",
        ASTRO_GAME="game",
        ASTRO_CHAT="chat",
        write_conf_keys=lambda *_args, **_kwargs: False,
    )
    window = SimpleNamespace(
        _core=core,
        astro_game=FakeToggle(),
        latency=FakeSpin(),
        save_devices=FakeButton(),
    )
    window._devices_settled = lambda: False
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
        _core=SimpleNamespace(set_moonlight_mic=lambda _source: False),
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
        _core=SimpleNamespace(set_moonlight_mic=lambda source: source == "b2_mic"),
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
