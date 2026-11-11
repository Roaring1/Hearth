"""The Screen Share overview shown before a capture session exists.

This window is intentionally read-only and cheap: opening it never probes a
capture device, starts a process, watches PipeWire, changes audio routing, or
starts a screen share. Device checks and session controls belong to the later
capture core. Its job today is to explain the Hearth/Vesktop workflow and make
the current audio-routing boundary explicit.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402 - must follow require_version

from hearth import share as share_mod  # noqa: E402


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


class CaptureOverviewWindow(Gtk.Window):
    """A plain-language overview; no capture-specific work runs in this window."""

    def __init__(self, parent: Gtk.Window) -> None:
        super().__init__(title="Hearth Screen Share", transient_for=parent)
        self.add_css_class("hearth")
        self.set_default_size(440, -1)
        self.set_hide_on_close(True)

        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        column.set_margin_top(14)
        column.set_margin_bottom(14)
        column.set_margin_start(14)
        column.set_margin_end(14)

        title = Gtk.Label(label="SCREEN SHARE", xalign=0.0)
        title.add_css_class("setup-verdict")
        self.title_label = title
        column.append(title)
        column.append(
            _note(
                "Set up a clean video window for friends to watch, then choose "
                "that window in Vesktop. Hearth prepares the picture; Vesktop "
                "starts the actual share."
            )
        )

        device = _section("VIDEO INPUT")
        device.append(_note("Capture device check is not available yet"))
        device.append(
            _note(
                "Opening this overview does not access a video device. Once capture "
                "support is ready, Hearth will check the device when you choose to start."
            )
        )
        column.append(device)

        audio = _section("WHAT FRIENDS HEAR")
        audio_title = Gtk.Label(label="", xalign=0.0)
        audio_title.add_css_class("listener")
        self.audio_selection_label = audio_title
        audio.append(audio_title)
        audio.append(
            _note(
                "This is the saved selection, not a live audio check. Vesktop "
                "currently receives its selected sources directly; the SCREEN "
                "SHARE mixer fader is not connected to that path yet. This "
                "overview does not change your audio settings."
            )
        )
        column.append(audio)

        steps = _section("WHEN CAPTURE IS AVAILABLE")
        self.workflow_steps: list[Gtk.Label] = []
        self.workflow_details: list[Gtk.Label] = []
        for number, heading, detail in (
            (
                "1",
                "Start capture in Hearth",
                "Hearth will open a separate video-only window named Hearth - Share Output.",
            ),
            (
                "2",
                "Choose the window in Vesktop",
                "Open Share Screen in Vesktop. In the desktop picker on Wayland, choose Hearth - Share Output, not the whole display.",
            ),
            (
                "3",
                "Enable audio and start sharing",
                "Enable Share Audio in Vesktop, check the preview, then start sharing yourself.",
            ),
        ):
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            badge = Gtk.Label(label=number, xalign=0.5)
            badge.add_css_class("bind")
            row.append(badge)
            text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            name = Gtk.Label(label=heading, xalign=0.0)
            name.add_css_class("listener")
            self.workflow_steps.append(name)
            text.append(name)
            detail_label = _note(detail)
            self.workflow_details.append(detail_label)
            text.append(detail_label)
            row.append(text)
            steps.append(row)
        steps.append(
            _note(
                "Hearth never presses Go Live or starts a Vesktop share for you. "
                "You remain in control of when other people can see the video."
            )
        )
        column.append(steps)

        self.set_child(column)
        self.refresh()

    def refresh(self) -> None:
        """Refresh the saved selection when shown; never query the live graph."""
        selected = [
            share_mod.BUSES.get(item.strip().lower(), item.strip())
            for item in share_mod.selection()
            if item.strip()
        ]
        saved_sources = " + ".join(dict.fromkeys(selected)) if selected else "Nothing selected"
        self.audio_selection_label.set_text(f"Saved Vesktop audio selection: {saved_sources}")
