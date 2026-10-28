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
from types import ModuleType

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


def test_settings_restore_reinstates_exact_original_bytes_and_mode(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    module = _load_func_check()
    monkeypatch.setattr(module.subprocess, "run", _fake_pactl([]))
    target = module.paths.settings_file()
    target.parent.mkdir(parents=True, exist_ok=True)
    original = b'{\n  "mixer_minimised": ["vm_music"]\n}\n'
    target.write_bytes(original)
    target.chmod(0o640)
    app = module.FuncApp()

    target.write_bytes(b'{"mixer_minimised": []}\n')
    app.restore_settings_state()

    assert target.read_bytes() == original
    assert target.stat().st_mode & 0o777 == 0o640


def test_settings_restore_removes_a_file_created_by_the_test(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    module = _load_func_check()
    monkeypatch.setattr(module.subprocess, "run", _fake_pactl([]))
    target = module.paths.settings_file()
    app = module.FuncApp()
    parent_existed = app._settings_parent_existed

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text('{"created_by_functional_test": true}\n')
    app.restore_settings_state()

    assert not target.exists()
    if not parent_existed:
        assert not target.parent.exists()
