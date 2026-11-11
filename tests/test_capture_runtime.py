"""V4L2 resolution seam tests consume fake inventories only."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from hearth.capture_devices import (
    CaptureDevice,
    CaptureNode,
    V4L2Capabilities,
)
from hearth.capture_provider import CaptureInventory, CaptureProbeIssue
from hearth.capture_runtime import CaptureDeviceResolutionError, V4L2CaptureResolver
from hearth.capture_session import CapturePlan, UsbIdentity, VideoMode

CARD = UsbIdentity("1abc", "02ef", serial_number="GUV302G-001")
MODE = VideoMode("MJPG", 1920, 1080, 60)
OTHER_MODE = VideoMode("MJPG", 1280, 720, 60)


def _device(identity: UsbIdentity = CARD, path: str = "/dev/video4") -> CaptureDevice:
    node = CaptureNode(
        path,
        "Fixture capture card",
        identity,
        V4L2Capabilities(True, False, False),
        (MODE, OTHER_MODE),
    )
    return CaptureDevice(identity, (node,))


@dataclass
class FakeInventory:
    snapshot: CaptureInventory
    calls: int = 0

    def discover(self) -> CaptureInventory:
        self.calls += 1
        return self.snapshot


def test_resolver_selects_exact_device_identity_node_and_mode() -> None:
    provider = FakeInventory(
        CaptureInventory(
            devices=(
                _device(UsbIdentity("1abc", "02ef", serial_number="other-card"), "/dev/video6"),
                _device(),
            )
        )
    )
    result = V4L2CaptureResolver(provider).probe_device(CapturePlan(CARD, MODE))

    assert result.identity == CARD
    assert result.video_node_path == "/dev/video4"
    assert result.mode == MODE
    assert result.audio_source_selector is None
    assert provider.calls == 1


def test_resolver_fails_when_identity_is_absent() -> None:
    provider = FakeInventory(CaptureInventory(devices=(_device(),)))
    absent = UsbIdentity("1abc", "02ef", serial_number="different-serial")

    with pytest.raises(CaptureDeviceResolutionError, match="was not found"):
        V4L2CaptureResolver(provider).probe_device(CapturePlan(absent, MODE))


def test_resolver_fails_closed_on_incomplete_inventory() -> None:
    provider = FakeInventory(
        CaptureInventory(
            devices=(_device(),),
            issues=(CaptureProbeIssue("/dev/video8", "USB identity", "permission denied"),),
        )
    )

    with pytest.raises(CaptureDeviceResolutionError, match="inventory is incomplete"):
        V4L2CaptureResolver(provider).probe_device(CapturePlan(CARD, MODE))


def test_resolver_requires_exact_enumerated_mode() -> None:
    unavailable = VideoMode("YUYV", 1920, 1080, 60)
    provider = FakeInventory(CaptureInventory(devices=(_device(),)))

    with pytest.raises(CaptureDeviceResolutionError, match="mode is no longer available"):
        V4L2CaptureResolver(provider).probe_device(CapturePlan(CARD, unavailable))


def test_resolver_refuses_multiple_eligible_video_nodes_on_same_usb_device() -> None:
    first = _device().nodes[0]
    second = CaptureNode(
        "/dev/video5",
        "Fixture capture card secondary",
        CARD,
        first.capabilities,
        first.modes,
    )
    device = CaptureDevice(CARD, (first, second))
    provider = FakeInventory(CaptureInventory(devices=(device,)))

    with pytest.raises(CaptureDeviceResolutionError, match="user selection required"):
        V4L2CaptureResolver(provider).probe_device(CapturePlan(CARD, MODE))


def test_injected_inventory_provider_is_called_exactly_once_per_probe() -> None:
    provider = FakeInventory(CaptureInventory(devices=(_device(),)))
    resolver = V4L2CaptureResolver(provider)

    resolver.probe_device(CapturePlan(CARD, MODE))
    resolver.probe_device(CapturePlan(CARD, OTHER_MODE))

    assert provider.calls == 2
