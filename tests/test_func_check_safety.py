"""Safety tests for the developer's live functional-check harness.

The harness is intentionally GTK4-marked, but all pactl interactions here are
mocked. The tests prove cleanup preserves user state without changing a real
sink or needing a running audio graph.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
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
    monkeypatch.setattr(mixer.pactl, "modules", lambda: live_modules)
    collector = FakeCollector()
    strip = SimpleNamespace(
        win=SimpleNamespace(collector=collector),
        channel=SimpleNamespace(sink="vm_game"),
        _out_checks={
            "vm_chat": FakeCheck(False),
            "vm_music": FakeCheck(True),
        },
        _routing=False,
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

    monkeypatch.setattr(mixer.pactl, "modules", lambda: [])
    check = FakeCheck()
    strip = SimpleNamespace(
        win=SimpleNamespace(collector=FakeCollector()),
        channel=SimpleNamespace(sink="vm_game"),
        _out_checks={"vm_chat": check},
        _routing=False,
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

    def fail_to_load(*_args, **_kwargs):
        raise mixer.CommandError(["pactl", "load-module"], stderr="not available")

    monkeypatch.setattr(mixer.pactl, "load_loopback", fail_to_load)
    check = FakeCheck()
    strip = SimpleNamespace(
        _routing=False,
        channel=SimpleNamespace(sink="mic_b1"),
        win=SimpleNamespace(cfg={"latency_msec": 12}),
    )

    mixer.Strip._on_mic_in(strip, check, "external_mic")

    assert check.active is False
    assert check.tooltip is not None
    assert "Could not create this route" in check.tooltip
    assert strip._routing is False


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
