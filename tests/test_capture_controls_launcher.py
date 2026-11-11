"""The Capture Card launcher opens the existing lazy overview, never capture."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.mark.gui
def test_capture_overview_action_creates_and_reuses_the_lazy_window(monkeypatch) -> None:
    from hearth.ui.gtk4 import app as app_module

    events: list[str] = []

    class FakeMixerWindow:
        def __init__(self, _app) -> None:
            events.append("mixer-created")

        def open_capture_overview(self) -> None:
            events.append("overview-presented")

    monkeypatch.setattr(app_module, "MixerWindow", FakeMixerWindow)
    fake_app = SimpleNamespace(window=None)

    assert app_module.MixerApp._show_capture_overview(fake_app) is False
    assert events == ["mixer-created", "overview-presented"]
    assert fake_app.window is not None

    app_module.MixerApp._show_capture_overview(fake_app)
    assert events == ["mixer-created", "overview-presented", "overview-presented"]


@pytest.mark.gui
def test_existing_instance_control_handler_schedules_the_same_overview(monkeypatch) -> None:
    from hearth.ui.gtk4 import app as app_module

    callbacks = []
    monkeypatch.setattr(
        app_module.GLib, "idle_add", lambda callback: callbacks.append(callback) or 1
    )
    shown: list[bool] = []
    fake_app = SimpleNamespace(_show_capture_overview=lambda: shown.append(True) or False)

    assert app_module.MixerApp._ctl_capture_overview(fake_app) == "ok"
    assert shown == []
    assert len(callbacks) == 1
    callbacks[0]()
    assert shown == [True]


def test_desktop_shortcut_opens_the_capture_setup_without_a_terminal() -> None:
    launcher = (
        Path(__file__).resolve().parents[1]
        / "data/applications/io.github.roaring1.Hearth.Capture.desktop"
    )
    text = launcher.read_text(encoding="utf-8")

    assert "Name=Use Capture Card" in text
    assert "Exec=hearth --capture-controls" in text
    assert "Terminal=false" in text
    assert "StartupWMClass=io.github.roaring1.Hearth" in text


@pytest.mark.gui
def test_lazy_ui_entrypoint_passes_capture_overview_arguments(monkeypatch) -> None:
    from hearth.ui import run_app
    from hearth.ui.gtk4 import app as app_module

    received: list[list[str]] = []
    monkeypatch.setattr(
        app_module, "main", lambda argv=None: received.append(list(argv or [])) or 0
    )

    assert run_app(["hearth", "--open-capture-overview"]) == 0
    assert received == [["hearth", "--open-capture-overview"]]


@pytest.mark.gui
def test_gapplication_command_line_opens_only_the_overview_for_desktop_intent() -> None:
    from hearth.ui.gtk4 import app as app_module

    calls: list[str] = []

    class FakeCommandLine:
        def get_arguments(self):
            return ["hearth", "--open-capture-overview"]

        def set_exit_status(self, status: int) -> None:
            calls.append(f"exit:{status}")

    fake_app = SimpleNamespace(
        _show_capture_overview=lambda: calls.append("overview"),
        activate=lambda: calls.append("main"),
    )
    assert app_module.MixerApp.do_command_line(fake_app, FakeCommandLine()) == 0
    assert calls == ["overview", "exit:0"]


@pytest.mark.gui
def test_gapplication_command_line_preserves_normal_main_activation() -> None:
    from hearth.ui.gtk4 import app as app_module

    calls: list[str] = []

    class FakeCommandLine:
        def get_arguments(self):
            return ["hearth"]

        def set_exit_status(self, status: int) -> None:
            calls.append(f"exit:{status}")

    fake_app = SimpleNamespace(
        _show_capture_overview=lambda: calls.append("overview"),
        activate=lambda: calls.append("main"),
    )
    assert app_module.MixerApp.do_command_line(fake_app, FakeCommandLine()) == 0
    assert calls == ["main", "exit:0"]
