"""Hardware-free GTK tests for the read-only Capture Controls preflight."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from hearth.capture_devices import CaptureDevice, CaptureNode, V4L2Capabilities
from hearth.capture_provider import CaptureInventory
from hearth.capture_session import UsbIdentity, VideoMode


class ImmediateThread:
    """Run the worker deterministically while preserving its thread contract."""

    def __init__(self, *, target, args, name: str) -> None:
        self.target = target
        self.args = args
        self.name = name
        self.started = False

    def start(self) -> None:
        self.started = True
        self.target(*self.args)

    def is_alive(self) -> bool:
        return False

    def join(self, timeout: float | None = None) -> None:
        return None


class PendingThread(ImmediateThread):
    def start(self) -> None:
        self.started = True

    def run(self) -> None:
        self.target(*self.args)
        self.started = False

    def is_alive(self) -> bool:
        return self.started


def _inventory() -> CaptureInventory:
    identity = UsbIdentity("2f00", "0001", serial_number="test-card")
    node = CaptureNode(
        "/dev/video-fixture",
        "IOGEAR UpStream 4K",
        identity,
        V4L2Capabilities(
            video_capture=True, video_capture_multiplanar=False, metadata_capture=False
        ),
        (VideoMode("MJPG", 1920, 1080, 60),),
    )
    return CaptureInventory(devices=(CaptureDevice(identity, (node,)),), issues=())


def _window(inventory: CaptureInventory | None = None):
    from hearth.ui.gtk4 import app as app_module
    from hearth.ui.gtk4.capture_controls import CaptureControlsWindow

    Gtk = app_module.Gtk

    calls: list[int] = []

    class Provider:
        def discover(self) -> CaptureInventory:
            calls.append(1)
            return inventory if inventory is not None else _inventory()

    def idle_add(callback, *args):
        callback(*args)
        return 1

    window = CaptureControlsWindow(
        Gtk.Window(),
        provider_factory=Provider,
        selection_provider=lambda: ["game", "music"],
        idle_add=idle_add,
        thread_factory=ImmediateThread,
    )
    return window, calls


@pytest.mark.gui
def test_capture_controls_is_read_only_and_refreshes_on_explicit_request() -> None:
    window, calls = _window()
    assert calls == []
    assert window.device_status.get_text() == "Ready to check video inputs"
    assert window.session_status.get_text() == "Not started"
    assert window.refresh_button.get_label() == "REFRESH"

    assert window.refresh() is True
    assert calls == [1]
    assert window.audio_selection_label.get_text() == "Saved Vesktop audio selection: Game + Music"
    assert window.device_status.get_text() == "1 USB video input found"
    assert (
        "IOGEAR UpStream 4K" in window.device_results.get_first_child().get_first_child().get_text()
    )
    assert (
        "1920 x 1080 at 60 fps (MJPG)"
        in window.device_results.get_first_child().get_last_child().get_text()
    )

    # No nonfunctional Start/Stop button is exposed in this preflight-only slice.
    assert window.get_child() is not None
    assert window.session_status.get_text() == "Not started"
    window.shutdown()
    window.destroy()


@pytest.mark.gui
def test_capture_controls_does_not_overlap_refresh_requests() -> None:
    from hearth.ui.gtk4 import app as app_module
    from hearth.ui.gtk4.capture_controls import CaptureControlsWindow

    Gtk = app_module.Gtk

    calls: list[int] = []

    class Provider:
        def discover(self) -> CaptureInventory:
            calls.append(1)
            return _inventory()

    threads: list[PendingThread] = []

    def factory(**kwargs):
        thread = PendingThread(**kwargs)
        threads.append(thread)
        return thread

    callbacks: list[tuple[Any, tuple[Any, ...]]] = []
    window = CaptureControlsWindow(
        Gtk.Window(),
        provider_factory=Provider,
        selection_provider=lambda: [],
        idle_add=lambda callback, *args: callbacks.append((callback, args)) or 1,
        thread_factory=factory,
    )
    assert window.refresh() is True
    assert window.refresh() is False
    assert len(threads) == 1
    assert window.refresh_button.get_sensitive() is False

    threads[0].run()
    callback, args = callbacks.pop()
    callback(*args)
    assert calls == [1]
    assert window.refresh_button.get_sensitive() is True
    window.shutdown()
    window.destroy()


@pytest.mark.gui
def test_capture_controls_explains_empty_or_incomplete_inventory() -> None:
    empty = CaptureInventory()
    window, calls = _window(empty)
    window.refresh()
    assert calls == [1]
    assert window.device_status.get_text() == "No USB video input found"
    window.shutdown()
    window.destroy()

    incomplete = CaptureInventory(
        devices=(),
        issues=(SimpleNamespace(message="untrusted path"),),
    )
    window, _calls = _window(incomplete)
    window.refresh()
    assert window.device_status.get_text() == "No verified USB video input is ready"
    assert "not guessed" in window.device_detail.get_text()
    window.shutdown()
    window.destroy()


@pytest.mark.gui
def test_capture_controls_hides_host_command_details_on_inventory_failure() -> None:
    from hearth.ui.gtk4 import app as app_module
    from hearth.ui.gtk4.capture_controls import CaptureControlsWindow

    Gtk = app_module.Gtk

    class Provider:
        def discover(self):
            raise RuntimeError("/dev/video7 private host detail")

    window = CaptureControlsWindow(
        Gtk.Window(),
        provider_factory=Provider,
        selection_provider=lambda: [],
        idle_add=lambda callback, *args: callback(*args) or 1,
        thread_factory=ImmediateThread,
    )
    window.refresh()
    assert window.device_status.get_text() == "Device check could not be completed"
    assert "/dev/video7" not in window.device_detail.get_text()
    window.shutdown()
    window.destroy()


@pytest.mark.gui
def test_shutdown_discards_a_late_inventory_result() -> None:
    from hearth.ui.gtk4 import app as app_module
    from hearth.ui.gtk4.capture_controls import CaptureControlsWindow

    Gtk = app_module.Gtk
    callbacks: list[tuple[Any, tuple[Any, ...]]] = []

    class Provider:
        def discover(self) -> CaptureInventory:
            return _inventory()

    threads: list[PendingThread] = []

    def factory(**kwargs):
        thread = PendingThread(**kwargs)
        threads.append(thread)
        return thread

    window = CaptureControlsWindow(
        Gtk.Window(),
        provider_factory=Provider,
        selection_provider=lambda: [],
        idle_add=lambda callback, *args: callbacks.append((callback, args)) or 1,
        thread_factory=factory,
    )
    assert window.refresh() is True
    threads[0].run()
    assert len(callbacks) == 1
    window.shutdown()
    callback, args = callbacks.pop()
    assert callback(*args) == app_module.GLib.SOURCE_REMOVE
    assert window.device_status.get_text() == "Checking video inputs..."
    window.destroy()
