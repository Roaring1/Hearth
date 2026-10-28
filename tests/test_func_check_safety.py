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
        _route_errors={},
    )

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
    check = FakeCheck()
    strip = SimpleNamespace(
        win=SimpleNamespace(collector=FakeCollector()),
        channel=SimpleNamespace(sink="vm_game"),
        _out_checks={"vm_chat": check},
        _routing=False,
        _route_errors={},
    )

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
    monkeypatch.setattr(mixer.pactl, "modules", lambda **_kwargs: modules)

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
        _out_checks={"vm_chat": check},
        channel=SimpleNamespace(sink="vm_game"),
        win=SimpleNamespace(collector=FakeCollector()),
    )
    strip._finish_route_job = lambda *args: mixer.Strip._finish_route_job(strip, *args)
    strip._route_settled = lambda: mixer.Strip._route_settled(strip)

    def operation() -> None:
        operation_thread.append(threading.get_ident())
        finished.set()

    mixer.Strip._start_route_job(strip, operation, check, True, route_sink="vm_chat")

    assert not check.sensitive
    assert finished.wait(timeout=2)
    assert callback_ready.wait(timeout=2)
    assert operation_thread[0] != caller
    assert check.sensitive is False

    callback, args = queued[0]
    callback(*args)

    assert strip._route_busy is False
    assert check.sensitive is True
    assert check.active is True


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
