"""Concrete fail-closed V4L2 resolution seam for a future CaptureRuntime.

This resolver is intentionally narrower than the full runtime. It performs one
explicit inventory request, selects only by stable USB identity, resolves a
single eligible capture node, and verifies the exact previously enumerated
mode. Audio validation/routes and the viewer/inhibitor lifecycle remain
injected into the wider CaptureRuntime until their real adapters are ready.
"""

from __future__ import annotations

from typing import Protocol

from hearth.capture_devices import CaptureDevice, choose_capture_node
from hearth.capture_provider import CaptureInventory, V4L2InventoryProvider
from hearth.capture_session import CapturePlan, ResolvedCaptureDevice
from hearth.errors import HearthError


class CaptureInventorySource(Protocol):
    """One-shot inventory provider; implementations must not start a watcher."""

    def discover(self) -> CaptureInventory: ...


class CaptureDeviceResolutionError(HearthError):
    """The selected USB device/node/mode cannot be resolved unambiguously."""


class V4L2CaptureResolver:
    """Resolve a persisted USB identity to a fresh node/mode at Start time."""

    def __init__(self, inventory: CaptureInventorySource | None = None) -> None:
        self._inventory = inventory or V4L2InventoryProvider()

    def probe_device(self, plan: CapturePlan) -> ResolvedCaptureDevice:
        """Run one inventory check and resolve exactly the requested capture mode."""
        try:
            snapshot = self._inventory.discover()
        except Exception as exc:
            raise CaptureDeviceResolutionError(f"video device inventory failed: {exc}") from exc

        # An incomplete snapshot can hide a second matching node whose USB
        # identity could not be read. Refuse to select from a partial inventory.
        if snapshot.issues:
            first = snapshot.issues[0]
            raise CaptureDeviceResolutionError(
                "video device inventory is incomplete; "
                f"{first.device_path} ({first.stage}): {first.message}"
            )

        matching = tuple(
            device
            for device in snapshot.devices
            if device.identity.stable_key == plan.device.stable_key
        )
        if not matching:
            raise CaptureDeviceResolutionError(
                "selected USB capture device was not found; reconnect it and check again"
            )
        if len(matching) != 1:
            raise CaptureDeviceResolutionError(
                "selected USB identity appears more than once in the inventory"
            )
        device: CaptureDevice = matching[0]
        try:
            node = choose_capture_node(device)
        except ValueError as exc:
            raise CaptureDeviceResolutionError(str(exc)) from exc

        if plan.preferred_mode not in node.modes:
            raise CaptureDeviceResolutionError(
                "the selected video mode is no longer available on this device; "
                "refresh the available modes before starting capture"
            )
        # The live audio source selector is intentionally not guessed here.
        # The PipeWire identity validator must resolve it separately.
        return ResolvedCaptureDevice(
            identity=device.identity,
            video_node_path=node.device_path,
            mode=plan.preferred_mode,
            audio_source_selector=None,
        )
