"""PipeWire JSON parsing uses synthetic snapshots and a fake command runner."""

from __future__ import annotations

import json
import math
import subprocess

import pytest

from hearth import proc
from hearth.capture_devices import (
    AudioAssociationStatus,
    CaptureNode,
    V4L2Capabilities,
    associate_audio_source,
    group_capture_devices,
)
from hearth.capture_pipewire import (
    PipeWireAudioProvider,
    PipeWireDiscoveryError,
    parse_pw_dump_audio_inventory,
)
from hearth.capture_session import UsbIdentity, VideoMode

USB_DEVICE = {
    "id": 17,
    "type": "PipeWire:Interface:Device",
    "info": {
        "props": {
            "device.bus": "usb",
            "device.vendor.id": 6844,
            "device.product.id": 751,
            "device.serial": "GUV302G-001",
            "device.bus-path": "pci-0000:00:14.0-usb-0:5:1.0",
            "device.description": "IOGEAR UpStream Audio",
        }
    },
}
USB_SOURCE = {
    "id": 29,
    "type": "PipeWire:Interface:Node",
    "info": {
        "props": {
            "media.class": "Audio/Source",
            "node.name": "alsa_input.usb-IOGEAR_GUV302G.analog-stereo",
            "node.description": "IOGEAR Capture Audio",
            "device.id": "17",
        }
    },
}


@pytest.fixture(autouse=True)
def restore_process_runner():
    yield
    proc.set_runner(None)


def test_parse_pw_dump_joins_node_to_device_by_snapshot_id_only() -> None:
    text = json.dumps([USB_DEVICE, USB_SOURCE])

    inventory = parse_pw_dump_audio_inventory(text)

    assert inventory.complete
    assert len(inventory.sources) == 1
    source = inventory.sources[0]
    assert source.source_selector == "alsa_input.usb-IOGEAR_GUV302G.analog-stereo"
    assert source.source_selector != str(USB_SOURCE["id"])
    assert source.display_name == "IOGEAR Capture Audio"
    assert source.identity == UsbIdentity(
        "1abc", "02ef", serial_number="GUV302G-001", port_path="pci-0000:00:14.0-usb-0:5"
    )


def test_pipewire_audio_inventory_matches_capture_by_usb_identity() -> None:
    inventory = parse_pw_dump_audio_inventory(json.dumps([USB_DEVICE, USB_SOURCE]))
    video = group_capture_devices(
        (
            CaptureNode(
                "/dev/video4",
                "IOGEAR video",
                UsbIdentity("1abc", "02ef", serial_number="GUV302G-001"),
                V4L2Capabilities(True, False, False),
                (VideoMode("MJPG", 1920, 1080, 60),),
            ),
        )
    )[0]

    result = associate_audio_source(video, inventory.sources if inventory.complete else None)

    assert result.status is AudioAssociationStatus.MATCHED
    assert result.selected == inventory.sources[0]


def test_non_usb_and_virtual_sources_are_not_capture_card_audio_candidates() -> None:
    pci_device = {
        "id": 18,
        "type": "PipeWire:Interface:Device",
        "info": {"props": {"device.bus": "pci", "device.description": "Built-in audio"}},
    }
    pci_source = {
        "id": 30,
        "type": "PipeWire:Interface:Node",
        "info": {
            "props": {
                "media.class": "Audio/Source",
                "node.name": "alsa_input.pci-built-in",
                "device.id": 18,
            }
        },
    }
    virtual_source = {
        "id": 31,
        "type": "PipeWire:Interface:Node",
        "info": {
            "props": {
                "media.class": "Audio/Source",
                "node.name": "virtual_capture",
                "node.virtual": True,
            }
        },
    }

    inventory = parse_pw_dump_audio_inventory(json.dumps([pci_device, pci_source, virtual_source]))

    assert inventory.complete
    assert inventory.sources == ()


def test_nonvirtual_audio_source_without_any_device_metadata_is_incomplete() -> None:
    unknown_source = {
        "id": 33,
        "type": "PipeWire:Interface:Node",
        "info": {"props": {"media.class": "Audio/Source", "node.name": "mystery-source"}},
    }

    inventory = parse_pw_dump_audio_inventory(json.dumps([unknown_source]))

    assert not inventory.complete
    assert inventory.sources == ()
    assert inventory.issues[0].message == "Audio source has no device identity metadata"


def test_missing_device_reference_makes_inventory_incomplete_not_false_none() -> None:
    missing = {
        "id": 40,
        "type": "PipeWire:Interface:Node",
        "info": {
            "props": {
                "media.class": "Audio/Source",
                "node.name": "alsa_input.usb-missing-parent",
                "device.id": "999",
            }
        },
    }

    inventory = parse_pw_dump_audio_inventory(json.dumps([missing]))

    assert not inventory.complete
    assert inventory.sources == ()
    assert len(inventory.issues) == 1
    assert "not confirmed as USB" in inventory.issues[0].message


def test_multiple_usb_audio_nodes_remain_ambiguous_for_same_card() -> None:
    second = {
        "id": 30,
        "type": "PipeWire:Interface:Node",
        "info": {
            "props": {
                "media.class": "Audio/Source",
                "node.name": "alsa_input.usb-IOGEAR_GUV302G.second",
                "device.id": 17,
            }
        },
    }
    inventory = parse_pw_dump_audio_inventory(json.dumps([USB_DEVICE, USB_SOURCE, second]))
    video = group_capture_devices(
        (
            CaptureNode(
                "/dev/video4",
                "IOGEAR video",
                UsbIdentity("1abc", "02ef", serial_number="GUV302G-001"),
                V4L2Capabilities(True, False, False),
                (VideoMode("MJPG", 1920, 1080, 60),),
            ),
        )
    )[0]

    result = associate_audio_source(video, inventory.sources if inventory.complete else None)

    assert result.status is AudioAssociationStatus.AMBIGUOUS
    assert result.selected is None
    assert len(result.candidates) == 2


def test_incomplete_inventory_is_treated_as_unknown_for_matching() -> None:
    inventory = parse_pw_dump_audio_inventory(
        json.dumps(
            [
                USB_DEVICE,
                USB_SOURCE,
                {
                    "id": 40,
                    "type": "PipeWire:Interface:Node",
                    "info": {
                        "props": {
                            "media.class": "Audio/Source",
                            "node.name": "broken",
                            "device.id": "404",
                        }
                    },
                },
            ]
        )
    )
    video = group_capture_devices(
        (
            CaptureNode(
                "/dev/video4",
                "IOGEAR video",
                UsbIdentity("1abc", "02ef", serial_number="GUV302G-001"),
                V4L2Capabilities(True, False, False),
                (VideoMode("MJPG", 1920, 1080, 60),),
            ),
        )
    )[0]

    result = associate_audio_source(video, inventory.sources if inventory.complete else None)

    assert not inventory.complete
    assert result.status is AudioAssociationStatus.UNKNOWN


def test_pipewire_provider_uses_one_bounded_read_only_command() -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []

    def runner(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, json.dumps([USB_DEVICE, USB_SOURCE]), "")

    proc.set_runner(runner)
    inventory = PipeWireAudioProvider(timeout_s=1.5).discover()

    assert inventory.complete
    assert calls == [
        (
            ["pw-dump", "--no-colors"],
            {
                "capture_output": True,
                "text": True,
                "timeout": 1.5,
                "input": None,
                "env": None,
                "cwd": None,
                "check": False,
            },
        )
    ]


def test_pipewire_provider_wraps_a_bounded_command_timeout() -> None:
    def timeout_runner(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(argv, kwargs.get("timeout", 0))

    proc.set_runner(timeout_runner)
    with pytest.raises(PipeWireDiscoveryError, match="Could not inspect audio devices"):
        PipeWireAudioProvider(timeout_s=0.2).discover()


@pytest.mark.parametrize("timeout_s", [0, math.nan, math.inf])
def test_pipewire_provider_rejects_nonpositive_or_unbounded_timeout(
    timeout_s: float,
) -> None:
    with pytest.raises(ValueError, match="timeout_s must be positive and finite"):
        PipeWireAudioProvider(timeout_s=timeout_s)


def test_pipewire_provider_wraps_command_and_json_failures() -> None:
    def missing_runner(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError(argv[0])

    proc.set_runner(missing_runner)
    with pytest.raises(PipeWireDiscoveryError, match="Could not inspect audio devices"):
        PipeWireAudioProvider().discover()

    def invalid_json_runner(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 0, "not json", "")

    proc.set_runner(invalid_json_runner)
    with pytest.raises(PipeWireDiscoveryError, match="Could not parse audio device inventory"):
        PipeWireAudioProvider().discover()
