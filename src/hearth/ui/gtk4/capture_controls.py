"""Read-only Capture Controls preflight window.

Opening this window performs one bounded V4L2 inventory request. Refresh is
explicit and one-shot. The window does not start a stream, query PipeWire, or
change audio routing; those actions stay unavailable until the transactional
session has its production audio-route adapter.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
import logging
from threading import Thread
from typing import Any

from hearth import share as share_mod
from hearth.capture_devices import CaptureNode
from hearth.capture_provider import CaptureInventory, V4L2InventoryProvider

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402 - must follow require_version

ProviderFactory = Callable[[], V4L2InventoryProvider]
SelectionProvider = Callable[[], Sequence[str]]
IdleAdd = Callable[..., Any]
ThreadFactory = Callable[..., Any]
log = logging.getLogger(__name__)


def _note(text: str) -> Gtk.Label:
    label = Gtk.Label(label=text, xalign=0.0)
    label.add_css_class("setup-note")
    label.set_wrap(True)
    return label


def _section(title: str) -> Gtk.Box:
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    box.add_css_class("setup-box")
    heading = Gtk.Label(label=title, xalign=0.0)
    heading.add_css_class("setup-tag")
    box.append(heading)
    return box


def _fps(node: CaptureNode) -> str:
    modes: list[str] = []
    for mode in node.modes:
        rate = mode.fps_numerator / mode.fps_denominator
        rate_text = str(int(rate)) if rate.is_integer() else f"{rate:.3g}"
        modes.append(f"{mode.width} x {mode.height} at {rate_text} fps ({mode.pixel_format})")
    return ", ".join(modes)


def _device_title(node: CaptureNode) -> str:
    return node.card_label.strip() or "USB video device"


class CaptureControlsWindow(Gtk.Window):
    """Independent, read-only preflight window until Start has real adapters."""

    def __init__(
        self,
        parent: Gtk.Window,
        *,
        provider_factory: ProviderFactory | None = None,
        selection_provider: SelectionProvider | None = None,
        idle_add: IdleAdd | None = None,
        thread_factory: ThreadFactory | None = None,
    ) -> None:
        super().__init__(title="Hearth - Capture Controls", transient_for=parent)
        self.add_css_class("hearth")
        self.set_default_size(540, 430)
        self.set_hide_on_close(True)
        self._provider_factory = provider_factory or V4L2InventoryProvider
        self._selection_provider = selection_provider or share_mod.selection
        self._idle_add = idle_add or GLib.idle_add
        self._thread_factory = thread_factory or Thread
        self._generation = 0
        self._checking = False
        self._shutting_down = False
        self._worker: Any | None = None

        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        column.set_margin_top(14)
        column.set_margin_bottom(14)
        column.set_margin_start(14)
        column.set_margin_end(14)

        heading = Gtk.Label(label="CAPTURE CONTROLS", xalign=0.0)
        heading.add_css_class("setup-verdict")
        column.append(heading)
        column.append(
            _note(
                "Check the video input here. This window is separate from the "
                "video friends will see, and opening it never starts capture."
            )
        )

        device_section = _section("VIDEO INPUT")
        status_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.device_status = Gtk.Label(label="Ready to check video inputs", xalign=0.0)
        self.device_status.set_hexpand(True)
        self.device_status.set_accessible_role(Gtk.AccessibleRole.STATUS)
        self.device_status.add_css_class("listener")
        status_row.append(self.device_status)
        self.refresh_button = Gtk.Button(label="REFRESH")
        self.refresh_button.set_tooltip_text("Run a one-time video-device check")
        self.refresh_button.update_property(
            [Gtk.AccessibleProperty.LABEL], ["Refresh video-device check"]
        )
        self.refresh_button.connect("clicked", lambda _button: self.refresh())
        status_row.append(self.refresh_button)
        device_section.append(status_row)
        self.device_results = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        device_section.append(self.device_results)
        self.device_detail = _note(
            "No device is accessed until this check runs. Refresh performs one "
            "new bounded inventory request; Hearth does not keep watching."
        )
        device_section.append(self.device_detail)
        column.append(device_section)

        session_section = _section("CAPTURE SESSION")
        self.session_status = Gtk.Label(label="Not started", xalign=0.0)
        self.session_status.add_css_class("listener")
        session_section.append(self.session_status)
        session_section.append(
            _note(
                "Starting a session is not available yet. Hearth still needs "
                "the production audio-route owner and verified cleanup path. "
                "No Start or Stop control is shown until those actions work."
            )
        )
        column.append(session_section)

        audio_section = _section("SCREEN-SHARE AUDIO")
        self.audio_selection_label = Gtk.Label(label="", xalign=0.0)
        self.audio_selection_label.add_css_class("listener")
        audio_section.append(self.audio_selection_label)
        audio_section.append(
            _note(
                "This is saved intent, not a live route check. Audio routing is "
                "not checked or changed here, and the mixer Screen Share fader "
                "is not yet the active Vesktop route."
            )
        )
        column.append(audio_section)

        self.set_child(column)

    def refresh(self) -> bool:
        """Refresh saved choices and start one inventory request; never overlap."""
        if self._shutting_down or self._checking:
            return False
        selected = [
            share_mod.BUSES.get(item.strip().lower(), item.strip())
            for item in self._selection_provider()
            if item.strip()
        ]
        saved_sources = " + ".join(dict.fromkeys(selected)) if selected else "Nothing selected"
        self.audio_selection_label.set_text(f"Saved Vesktop audio selection: {saved_sources}")
        self._generation += 1
        generation = self._generation
        self._checking = True
        self.device_status.set_text("Checking video inputs...")
        self.device_detail.set_text(
            "Reading device names and supported capture modes. No video frames "
            "are opened and no audio settings are changed."
        )
        self.refresh_button.set_sensitive(False)
        try:
            worker = self._thread_factory(
                target=self._discover,
                args=(generation,),
                name="hearth-capture-device-check",
            )
            self._worker = worker
            worker.start()
        except Exception:
            self._worker = None
            self._checking = False
            self.refresh_button.set_sensitive(True)
            self.device_status.set_text("Device check could not start")
            self.device_detail.set_text(
                "Hearth could not start the one-time device inventory. You can try Refresh again."
            )
            return False
        return True

    def _discover(self, generation: int) -> None:
        inventory: CaptureInventory | None = None
        failed = False
        try:
            inventory = self._provider_factory().discover()
        except Exception:
            # Command details and host paths stay in diagnostic logs, not UI.
            log.exception("Capture Controls video inventory failed")
            failed = True
        try:
            self._idle_add(self._apply_result, generation, inventory, failed)
        except Exception:
            # The GTK context may already be closing; shutdown invalidates this
            # generation and no widget may be touched from the worker thread.
            log.debug("Capture Controls result could not be queued", exc_info=True)

    def _apply_result(
        self,
        generation: int,
        inventory: CaptureInventory | None,
        failed: bool,
    ) -> bool:
        if self._shutting_down or generation != self._generation:
            return GLib.SOURCE_REMOVE
        self._checking = False
        self._worker = None
        self.refresh_button.set_sensitive(True)
        self._clear_results()
        if failed or inventory is None:
            self.device_status.set_text("Device check could not be completed")
            self.device_detail.set_text(
                "Hearth could not read the video-device inventory. Check that "
                "v4l-utils is installed and that your account can inspect video devices."
            )
            return GLib.SOURCE_REMOVE

        devices = [node for device in inventory.devices for node in device.capture_nodes]
        if devices:
            count = len(devices)
            self.device_status.set_text(f"{count} USB video input{'s' if count != 1 else ''} found")
            for node in devices:
                card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
                card.add_css_class("setup-box")
                name = Gtk.Label(label=_device_title(node), xalign=0.0)
                name.add_css_class("listener")
                card.append(name)
                card.append(_note(_fps(node) or "No discrete modes reported"))
                self.device_results.append(card)
        elif inventory.issues:
            self.device_status.set_text("No verified USB video input is ready")
        else:
            self.device_status.set_text("No USB video input found")

        if inventory.issues:
            self.device_detail.set_text(
                "Some video-device entries could not be verified safely. "
                "Hearth has not guessed a device or mode. Reconnect the capture "
                "device and use Refresh to check again."
            )
        elif devices:
            self.device_detail.set_text(
                "These are the modes reported by the device driver. A listed "
                "mode is not proof that HDMI video is present or that a session "
                "can start yet."
            )
        else:
            self.device_detail.set_text(
                "Connect the USB video device, then select Refresh. No device stream is being used."
            )
        return GLib.SOURCE_REMOVE

    def _clear_results(self) -> None:
        child = self.device_results.get_first_child()
        while child is not None:
            next_child = child.get_next_sibling()
            self.device_results.remove(child)
            child = next_child

    def shutdown(self) -> None:
        """Ignore any late result when the application is explicitly quitting."""
        self._shutting_down = True
        self._generation += 1
        worker = self._worker
        if worker is not None and worker.is_alive():
            # The provider has a three-second total subprocess budget. Do not
            # let app shutdown leave a probe child running in the background.
            worker.join(timeout=3.5)
        self._worker = None
