"""The one-shot V4L2 provider is tested with fake command output only."""

from __future__ import annotations

import subprocess

import pytest

from hearth import proc
from hearth.capture_provider import (
    CaptureDiscoveryError,
    V4L2InventoryProvider,
)

LISTING = """IOGEAR UpStream 4K: IOGEAR GUV302G (usb-0000:00:14.0-5):
    /dev/video4
    /dev/video5
"""
UDEV4 = """DEVNAME=/dev/video4
ID_BUS=usb
ID_VENDOR_ID=1abc
ID_MODEL_ID=02ef
ID_SERIAL_SHORT=GUV302G-001
ID_V4L_PRODUCT=IOGEAR UpStream 4K
"""
UDEV5 = """DEVNAME=/dev/video5
ID_BUS=usb
ID_VENDOR_ID=1abc
ID_MODEL_ID=02ef
ID_SERIAL_SHORT=GUV302G-001
ID_V4L_PRODUCT=IOGEAR metadata
"""
VIDEO_CAPS = """Device Caps     : 0x04200001
    Video Capture
    Streaming
"""
META_CAPS = """Device Caps     : 0x04800001
    Metadata Capture
    Streaming
"""
FORMATS = """[0]: 'MJPG' (Motion-JPEG)
    Size: Discrete 1920x1080
        Interval: Discrete 0.016s (60.000 fps)
"""


@pytest.fixture(autouse=True)
def restore_process_runner():
    yield
    proc.set_runner(None)


def test_discovery_is_one_shot_read_only_and_groups_video_and_metadata_nodes() -> None:
    responses = {
        ("v4l2-ctl", "--list-devices"): LISTING,
        ("udevadm", "info", "--query=property", "--name=/dev/video4"): UDEV4,
        ("udevadm", "info", "--query=property", "--name=/dev/video5"): UDEV5,
        ("v4l2-ctl", "--device=/dev/video4", "--all"): VIDEO_CAPS,
        ("v4l2-ctl", "--device=/dev/video5", "--all"): META_CAPS,
        (
            "v4l2-ctl",
            "--device=/dev/video4",
            "--list-formats-ext",
        ): FORMATS,
    }

    def runner(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        key = tuple(argv)
        assert key in responses, f"unexpected device-probe command: {key}"
        assert kwargs.get("timeout") == 2.0
        assert kwargs.get("capture_output") is True
        assert kwargs.get("check") is False
        return subprocess.CompletedProcess(argv, 0, responses[key], "")

    proc.set_runner(runner)
    inventory = V4L2InventoryProvider(timeout_s=2.0).discover()

    assert len(inventory.devices) == 1
    device = inventory.devices[0]
    assert tuple(node.device_path for node in device.nodes) == ("/dev/video4", "/dev/video5")
    assert tuple(node.device_path for node in inventory.capture_nodes) == ("/dev/video4",)
    assert len(inventory.issues) == 0


def test_known_non_usb_node_is_excluded_without_incomplete_usb_inventory() -> None:
    def runner(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if argv == ["v4l2-ctl", "--list-devices"]:
            return subprocess.CompletedProcess(argv, 0, LISTING, "")
        if argv == ["udevadm", "info", "--query=property", "--name=/dev/video4"]:
            return subprocess.CompletedProcess(argv, 0, UDEV4, "")
        if argv == ["v4l2-ctl", "--device=/dev/video4", "--all"]:
            return subprocess.CompletedProcess(argv, 0, VIDEO_CAPS, "")
        if argv == ["v4l2-ctl", "--device=/dev/video4", "--list-formats-ext"]:
            return subprocess.CompletedProcess(argv, 0, FORMATS, "")
        if argv == ["udevadm", "info", "--query=property", "--name=/dev/video5"]:
            return subprocess.CompletedProcess(argv, 0, "DEVNAME=/dev/video5\nID_BUS=pci\n", "")
        raise AssertionError(f"unexpected command: {argv}")

    proc.set_runner(runner)
    inventory = V4L2InventoryProvider().discover()

    assert len(inventory.capture_nodes) == 1
    assert inventory.capture_nodes[0].device_path == "/dev/video4"
    assert inventory.issues == ()


def test_top_level_listing_timeout_and_missing_binary_are_discovery_errors() -> None:
    def timeout_runner(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(argv, kwargs.get("timeout", 0))

    proc.set_runner(timeout_runner)
    with pytest.raises(CaptureDiscoveryError, match="Could not list video devices"):
        V4L2InventoryProvider(timeout_s=0.1).discover()

    def missing_runner(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError(argv[0])

    proc.set_runner(missing_runner)
    with pytest.raises(CaptureDiscoveryError, match="Could not list video devices"):
        V4L2InventoryProvider().discover()


def test_nonpositive_timeout_is_rejected() -> None:
    with pytest.raises(ValueError, match="timeout_s must be positive"):
        V4L2InventoryProvider(timeout_s=0)
