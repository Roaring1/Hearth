"""One-shot, read-only V4L2 discovery using Hearth's argv-only process seam.

This provider is called only when the user asks to open Capture Controls. It
runs bounded inventory/capability commands, never opens a device itself, and
has no watcher, timer or background lifecycle. Hardware-dependent behavior is
covered with an injected `hearth.proc` runner in tests.
"""

from __future__ import annotations

from dataclasses import dataclass

from hearth import proc
from hearth.capture_devices import (
    CaptureDevice,
    CaptureNode,
    group_capture_devices,
    make_capture_node,
    parse_udev_video_properties,
    parse_v4l2_capabilities,
    parse_video_device_listing,
)
from hearth.errors import CommandError, HearthError


@dataclass(frozen=True, slots=True)
class CaptureProbeIssue:
    """A visible per-node discovery problem; other nodes can still be inspected."""

    device_path: str
    stage: str
    message: str


@dataclass(frozen=True, slots=True)
class CaptureInventory:
    """One bounded discovery snapshot with explicit per-node failures."""

    devices: tuple[CaptureDevice, ...] = ()
    issues: tuple[CaptureProbeIssue, ...] = ()

    @property
    def capture_nodes(self) -> tuple[CaptureNode, ...]:
        return tuple(node for device in self.devices for node in device.capture_nodes)


class CaptureDiscoveryError(HearthError):
    """The top-level V4L2 inventory command could not run or be parsed."""


class V4L2InventoryProvider:
    """Run a single, read-only probe when requested; no periodic work is started."""

    def __init__(self, *, timeout_s: float = 3.0) -> None:
        if timeout_s <= 0:
            raise ValueError("timeout_s must be positive")
        self._timeout_s = timeout_s

    def _run(self, argv: list[str]) -> str:
        result = proc.run(argv, timeout=self._timeout_s)
        return result.stdout

    def discover(self) -> CaptureInventory:
        """Enumerate current USB video nodes and their per-node capabilities.

        `v4l2-ctl --list-devices` failure aborts the snapshot because there is
        no trustworthy inventory. A failure while inspecting one node is
        returned as a `CaptureProbeIssue`; it does not cause another node to
        be silently selected in its place.
        """
        try:
            listing = self._run(["v4l2-ctl", "--list-devices"])
            listed_nodes = parse_video_device_listing(listing)
        except (CommandError, ValueError) as exc:
            raise CaptureDiscoveryError(f"Could not list video devices: {exc}") from exc

        nodes: list[CaptureNode] = []
        issues: list[CaptureProbeIssue] = []
        for device_path, card_label in listed_nodes.items():
            try:
                udev_text = self._run(
                    ["udevadm", "info", "--query=property", f"--name={device_path}"]
                )
                udev = parse_udev_video_properties(udev_text)
            except (CommandError, ValueError) as exc:
                issues.append(CaptureProbeIssue(device_path, "USB identity", str(exc)))
                continue

            try:
                caps_text = self._run(["v4l2-ctl", f"--device={device_path}", "--all"])
                capabilities = parse_v4l2_capabilities(caps_text)
            except (CommandError, ValueError) as exc:
                issues.append(CaptureProbeIssue(device_path, "Device Caps", str(exc)))
                continue

            formats_text = ""
            if capabilities.capture_capable:
                try:
                    formats_text = self._run(
                        ["v4l2-ctl", f"--device={device_path}", "--list-formats-ext"]
                    )
                except CommandError as exc:
                    issues.append(CaptureProbeIssue(device_path, "Video formats", str(exc)))

            try:
                node = make_capture_node(device_path, card_label, udev, capabilities, formats_text)
            except ValueError as exc:
                issues.append(CaptureProbeIssue(device_path, "Video formats", str(exc)))
                continue

            nodes.append(node)
            if capabilities.capture_capable and not node.modes:
                issues.append(
                    CaptureProbeIssue(
                        device_path,
                        "Video formats",
                        "No discrete capture modes were reported by the driver",
                    )
                )

        return CaptureInventory(group_capture_devices(nodes), tuple(issues))
