"""Fixture-based V4L2/udev parsing; no device nodes are opened."""

from __future__ import annotations

import pytest

from hearth.capture_devices import (
    CaptureNode,
    choose_capture_node,
    group_capture_devices,
    make_capture_node,
    parse_udev_video_properties,
    parse_v4l2_capabilities,
    parse_v4l2_discrete_modes,
    parse_video_device_listing,
)
from hearth.capture_session import UsbIdentity, VideoMode

V4L2_LIST = """IOGEAR UpStream 4K: IOGEAR GUV302G (usb-0000:00:14.0-5):
    /dev/video4
    /dev/video5
    /dev/media2

Laptop Camera: Integrated Camera (usb-0000:00:14.0-7):
    /dev/video8
"""

UDEV_VIDEO4 = """DEVNAME=/dev/video4
ID_BUS=usb
ID_VENDOR_ID=1abc
ID_MODEL_ID=02ef
ID_SERIAL_SHORT=GUV302G-001
ID_V4L_PRODUCT=IOGEAR UpStream 4K
ID_V4L_CAPABILITIES=:capture:
"""

UDEV_VIDEO5 = """DEVNAME=/dev/video5
ID_BUS=usb
ID_VENDOR_ID=1abc
ID_MODEL_ID=02ef
ID_SERIAL_SHORT=GUV302G-001
ID_V4L_PRODUCT=IOGEAR UpStream 4K Metadata
ID_V4L_CAPABILITIES=:metadata:
"""

CAPTURE_CAPS = """Capabilities : 0x84a00001
    Video Capture
    Read/Write
    Streaming
Device Caps     : 0x04200001
    Video Capture
    Streaming
"""

METADATA_CAPS = """Capabilities : 0x84a00001
    Video Capture
    Metadata Capture
    Streaming
Device Caps     : 0x04800001
    Metadata Capture
    Streaming
"""

FORMATS = """ioctl: VIDIOC_ENUM_FMT
    Type: Video Capture

    [0]: 'MJPG' (Motion-JPEG, compressed)
        Size: Discrete 1920x1080
            Interval: Discrete 0.016s (60.000 fps)
            Interval: Discrete 0.033s (30.000 fps)
        Size: Discrete 1280x720
            Interval: Discrete 0.016s (60.000 fps)
    [1]: 'YUYV' (YUYV 4:2:2)
        Size: Stepwise 640x480 - 1920x1080 with step 16/8
            Interval: Stepwise 0.033s - 1.000s with step 0.001s
"""


def test_parse_v4l2_list_groups_nodes_and_ignores_media_node() -> None:
    listing = parse_video_device_listing(V4L2_LIST)
    assert listing == {
        "/dev/video4": "IOGEAR UpStream 4K: IOGEAR GUV302G (usb-0000:00:14.0-5)",
        "/dev/video5": "IOGEAR UpStream 4K: IOGEAR GUV302G (usb-0000:00:14.0-5)",
        "/dev/video8": "Laptop Camera: Integrated Camera (usb-0000:00:14.0-7)",
    }
    assert all("media" not in path for path in listing)


def test_udev_identity_uses_serial_and_falls_back_to_physical_port() -> None:
    properties = parse_udev_video_properties(UDEV_VIDEO4)
    assert properties.identity == UsbIdentity("1abc", "02ef", serial_number="GUV302G-001")
    assert properties.identity.stable_key.endswith(":serial:GUV302G-001")

    fallback = parse_udev_video_properties(
        "DEVNAME=/dev/video8\nID_BUS=usb\nID_VENDOR_ID=045e\nID_MODEL_ID=0811\nID_PATH=pci-0000:00:14.0-usb-0:7:1.0\n"
    )
    assert fallback.identity.serial_number is None
    assert fallback.identity.stable_key.endswith(":port:pci-0000:00:14.0-usb-0:7:1.0")


def test_udev_rejects_node_without_stable_usb_identity() -> None:
    with pytest.raises(ValueError, match="serial or physical port"):
        parse_udev_video_properties("DEVNAME=/dev/video4\nID_VENDOR_ID=1abc\nID_MODEL_ID=02ef\n")
    with pytest.raises(ValueError, match="/dev/videoN"):
        parse_udev_video_properties("DEVNAME=/dev/media0\n")


def test_parse_capabilities_prefers_device_caps_over_aggregate_card_caps() -> None:
    video = parse_v4l2_capabilities(CAPTURE_CAPS)
    metadata = parse_v4l2_capabilities(METADATA_CAPS)
    assert video.capture_capable
    assert not video.metadata_capture
    assert not metadata.capture_capable
    assert metadata.metadata_capture


def test_aggregate_capabilities_are_not_used_when_device_caps_are_absent() -> None:
    with pytest.raises(ValueError, match="aggregate Capabilities are insufficient"):
        parse_v4l2_capabilities("Capabilities : 0x84000001\n    Video Capture\n")


def test_non_usb_video_node_is_rejected() -> None:
    with pytest.raises(ValueError, match="not attached to a USB device"):
        parse_udev_video_properties(
            "DEVNAME=/dev/video4\nID_BUS=pci\nID_VENDOR_ID=1abc\nID_MODEL_ID=02ef\nID_PATH=pci-0000:00:14.0\n"
        )


def test_discrete_modes_are_parsed_exactly_and_stepwise_ranges_are_not_guessed() -> None:
    assert parse_v4l2_discrete_modes(FORMATS) == (
        VideoMode("MJPG", 1280, 720, 60),
        VideoMode("MJPG", 1920, 1080, 30),
        VideoMode("MJPG", 1920, 1080, 60),
    )


def test_metadata_only_node_is_not_given_video_modes() -> None:
    metadata = parse_udev_video_properties(UDEV_VIDEO5)
    caps = parse_v4l2_capabilities(METADATA_CAPS)
    node = make_capture_node("/dev/video5", "IOGEAR metadata", metadata, caps, FORMATS)
    assert node.modes == ()
    assert not node.eligible


def test_same_usb_device_groups_multiple_nodes_without_guessing_capture_node() -> None:
    identity = UsbIdentity("1abc", "02ef", serial_number="GUV302G-001")
    video = make_capture_node(
        "/dev/video4",
        "IOGEAR video",
        parse_udev_video_properties(UDEV_VIDEO4),
        parse_v4l2_capabilities(CAPTURE_CAPS),
        FORMATS,
    )
    metadata = make_capture_node(
        "/dev/video5",
        "IOGEAR metadata",
        parse_udev_video_properties(UDEV_VIDEO5),
        parse_v4l2_capabilities(METADATA_CAPS),
        FORMATS,
    )
    devices = group_capture_devices((metadata, video))
    assert len(devices) == 1
    assert devices[0].identity == identity
    assert tuple(node.device_path for node in devices[0].nodes) == (
        "/dev/video4",
        "/dev/video5",
    )
    assert choose_capture_node(devices[0]) is video


def test_two_eligible_capture_nodes_require_explicit_user_selection() -> None:
    first = make_capture_node(
        "/dev/video4",
        "IOGEAR video A",
        parse_udev_video_properties(UDEV_VIDEO4),
        parse_v4l2_capabilities(CAPTURE_CAPS),
        FORMATS,
    )
    second = CaptureNode(
        "/dev/video6", "IOGEAR video B", first.identity, first.capabilities, first.modes
    )
    device = group_capture_devices((first, second))[0]

    with pytest.raises(ValueError, match="user selection required"):
        choose_capture_node(device)
    assert choose_capture_node(device, device_path="/dev/video6") is second


def test_explicit_selection_cannot_choose_metadata_only_node() -> None:
    video = make_capture_node(
        "/dev/video4",
        "IOGEAR video",
        parse_udev_video_properties(UDEV_VIDEO4),
        parse_v4l2_capabilities(CAPTURE_CAPS),
        FORMATS,
    )
    metadata = make_capture_node(
        "/dev/video5",
        "IOGEAR metadata",
        parse_udev_video_properties(UDEV_VIDEO5),
        parse_v4l2_capabilities(METADATA_CAPS),
        FORMATS,
    )
    device = group_capture_devices((video, metadata))[0]

    with pytest.raises(ValueError, match="not an eligible"):
        choose_capture_node(device, device_path="/dev/video5")


def test_node_identity_mismatch_is_rejected() -> None:
    with pytest.raises(ValueError, match="does not match"):
        make_capture_node(
            "/dev/video4",
            "IOGEAR video",
            parse_udev_video_properties(UDEV_VIDEO5),
            parse_v4l2_capabilities(CAPTURE_CAPS),
            FORMATS,
        )
