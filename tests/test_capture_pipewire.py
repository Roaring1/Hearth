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
    PipeWireSnapshotProvider,
    parse_pw_dump_audio_inventory,
    parse_pw_dump_snapshot,
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


def _graph_fixture() -> list[dict[str, object]]:
    """Minimal pw-dump Node/Port/Link objects with snapshot-local IDs."""
    return [
        {
            "id": 50,
            "type": "PipeWire:Interface:Node",
            "info": {"props": {"node.name": "vm_game"}},
        },
        {
            "id": 51,
            "type": "PipeWire:Interface:Node",
            "info": {"props": {"node.name": "vm_share"}},
        },
        {
            "id": 60,
            "type": "PipeWire:Interface:Port",
            "info": {
                "direction": "output",
                "props": {
                    "node.id": 50,
                    "port.name": "monitor_FL",
                    "port.direction": "out",
                },
            },
        },
        {
            "id": 61,
            "type": "PipeWire:Interface:Port",
            "info": {
                "direction": "input",
                "props": {
                    "node.id": 51,
                    "port.name": "playback_FL",
                    "port.direction": "in",
                },
            },
        },
        {
            "id": 80,
            "type": "PipeWire:Interface:Link",
            "info": {
                "output-node-id": 50,
                "output-port-id": 60,
                "input-node-id": 51,
                "input-port-id": 61,
                "state": "active",
                "error": None,
                "props": {
                    "link.output.node": 50,
                    "link.output.port": 60,
                    "link.input.node": 51,
                    "link.input.port": 61,
                    "hearth.route.owner": "io.github.roaring1.Hearth",
                    "hearth.route.kind": "capture-session",
                    "hearth.route.id": "route-test-1",
                    "hearth.route.session": "session-test-1",
                    "object.serial": 9001,
                },
            },
        },
    ]


def test_parse_pw_dump_graph_resolves_stable_endpoints_and_route_markers() -> None:
    from hearth.capture_pipewire import parse_pw_dump_graph

    graph = parse_pw_dump_graph(json.dumps(_graph_fixture()))

    assert graph.complete
    assert len(graph.links) == 1
    link = graph.links[0]
    assert link.output.node_name == "vm_game"
    assert link.output.port_name == "monitor_FL"
    assert link.output.direction == "output"
    assert link.input.node_name == "vm_share"
    assert link.input.port_name == "playback_FL"
    assert link.input.direction == "input"
    assert {port.direction for port in graph.ports} == {"input", "output"}
    assert link.state == "active"
    assert link.route_owner == "io.github.roaring1.Hearth"
    assert link.route_kind == "capture-session"
    assert link.route_id == "route-test-1"
    assert link.hearth_owned
    assert link.session_owned
    assert link.lingering is None
    assert link.route_property("unknown") is None
    assert not hasattr(link, "id")
    assert not hasattr(link, "object_id")
    assert "object.serial" not in repr(link)


def test_parse_pw_dump_graph_resolves_reordered_snapshot_objects() -> None:
    from hearth.capture_pipewire import parse_pw_dump_graph

    graph = parse_pw_dump_graph(json.dumps(list(reversed(_graph_fixture()))))

    assert graph.complete
    assert graph.links[0].output.node_name == "vm_game"
    assert graph.links[0].input.node_name == "vm_share"


def test_parse_pw_dump_graph_fails_closed_for_unknown_nodes_or_ports() -> None:
    from hearth.capture_pipewire import parse_pw_dump_graph

    fixture = _graph_fixture()
    link = fixture[-1]
    info = link["info"]
    assert isinstance(info, dict)
    info["output-node-id"] = 404
    info_props = info["props"]
    assert isinstance(info_props, dict)
    info_props["link.output.node"] = 404

    graph = parse_pw_dump_graph(json.dumps(fixture))

    assert not graph.complete
    assert graph.links == ()
    assert graph.issues[0].message == "link references an unknown or ambiguous node"


def test_parse_pw_dump_graph_rejects_port_belonging_to_another_node() -> None:
    from hearth.capture_pipewire import parse_pw_dump_graph

    fixture = _graph_fixture()
    input_port = fixture[3]["info"]
    assert isinstance(input_port, dict)
    input_props = input_port["props"]
    assert isinstance(input_props, dict)
    input_props["node.id"] = 50

    graph = parse_pw_dump_graph(json.dumps(fixture))

    assert not graph.complete
    assert graph.links == ()
    assert graph.issues[0].message == "link port belongs to a different node"


def test_parse_pw_dump_graph_requires_output_to_input_port_directions() -> None:
    from hearth.capture_pipewire import parse_pw_dump_graph

    fixture = _graph_fixture()
    input_port = fixture[3]["info"]
    assert isinstance(input_port, dict)
    input_port["direction"] = "output"
    input_props = input_port["props"]
    assert isinstance(input_props, dict)
    input_props["port.direction"] = "out"

    graph = parse_pw_dump_graph(json.dumps(fixture))

    assert not graph.complete
    assert graph.links == ()
    assert graph.issues[0].message == "link port directions are not output-to-input"


def test_parse_pw_dump_graph_cross_checks_info_and_link_property_endpoint_ids() -> None:
    from hearth.capture_pipewire import parse_pw_dump_graph

    fixture = _graph_fixture()
    link_info = fixture[-1]["info"]
    assert isinstance(link_info, dict)
    properties = link_info["props"]
    assert isinstance(properties, dict)
    properties["link.output.port"] = 61

    graph = parse_pw_dump_graph(json.dumps(fixture))

    assert not graph.complete
    assert graph.links == ()
    assert graph.issues[0].message == "link endpoint properties disagree with link info"


def test_parse_pw_dump_graph_rejects_malformed_hearth_route_properties() -> None:
    from hearth.capture_pipewire import parse_pw_dump_graph

    fixture = _graph_fixture()
    link_info = fixture[-1]["info"]
    assert isinstance(link_info, dict)
    properties = link_info["props"]
    assert isinstance(properties, dict)
    properties["hearth.route.id"] = " "

    graph = parse_pw_dump_graph(json.dumps(fixture))

    assert not graph.complete
    assert graph.links == ()
    assert graph.issues[0].message == "Hearth route metadata is invalid"


def test_parse_pw_dump_graph_accepts_unowned_links_but_does_not_infer_owner() -> None:
    from hearth.capture_pipewire import parse_pw_dump_graph

    fixture = _graph_fixture()
    link_info = fixture[-1]["info"]
    assert isinstance(link_info, dict)
    properties = link_info["props"]
    assert isinstance(properties, dict)
    for key in tuple(properties):
        if key.startswith("hearth.route."):
            properties.pop(key)

    graph = parse_pw_dump_graph(json.dumps(fixture))

    assert graph.complete
    assert graph.links[0].route_owner is None
    assert graph.links[0].route_kind is None
    assert graph.links[0].route_id is None


def test_parse_pw_dump_graph_preserves_explicit_linger_and_passive_flags() -> None:
    from hearth.capture_pipewire import parse_pw_dump_graph

    fixture = _graph_fixture()
    properties = fixture[-1]["info"]["props"]
    assert isinstance(properties, dict)
    properties["object.linger"] = "false"
    properties["link.passive"] = "true"

    graph = parse_pw_dump_graph(json.dumps(fixture))

    assert graph.complete
    assert graph.links[0].lingering is False
    assert graph.links[0].passive is True

    properties["object.linger"] = "true"
    graph = parse_pw_dump_graph(json.dumps(fixture))
    assert graph.complete
    assert graph.links[0].lingering is True


def test_parse_pw_dump_graph_does_not_call_persistent_links_session_owned() -> None:
    from hearth.capture_pipewire import parse_pw_dump_graph

    fixture = _graph_fixture()
    properties = fixture[-1]["info"]["props"]
    assert isinstance(properties, dict)
    properties["object.linger"] = "true"

    graph = parse_pw_dump_graph(json.dumps(fixture))

    assert graph.complete
    assert graph.links[0].hearth_owned
    assert not graph.links[0].session_owned
    assert graph.links[0].lingering is True


def test_parse_pw_dump_graph_fails_closed_for_malformed_linger_property() -> None:
    from hearth.capture_pipewire import parse_pw_dump_graph

    fixture = _graph_fixture()
    properties = fixture[-1]["info"]["props"]
    assert isinstance(properties, dict)
    properties["object.linger"] = "sometimes"

    graph = parse_pw_dump_graph(json.dumps(fixture))

    assert not graph.complete
    assert graph.links == ()
    assert graph.issues[0].message == "PipeWire object.linger property is invalid"


def test_parse_pw_dump_snapshot_combines_audio_identity_and_graph_from_one_text() -> None:
    snapshot = parse_pw_dump_snapshot(json.dumps([USB_DEVICE, USB_SOURCE, *_graph_fixture()]))

    assert snapshot.complete
    assert snapshot.audio.complete
    assert snapshot.graph.complete
    assert snapshot.audio.sources[0].identity == UsbIdentity(
        "1abc", "02ef", serial_number="GUV302G-001", port_path="pci-0000:00:14.0-usb-0:5"
    )
    assert snapshot.graph.links[0].output.node_name == "vm_game"
    assert snapshot.graph.links[0].input.node_name == "vm_share"


def test_pipewire_snapshot_provider_runs_exactly_one_bounded_dump() -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []
    payload = [USB_DEVICE, USB_SOURCE, *_graph_fixture()]

    def runner(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, json.dumps(payload), "")

    proc.set_runner(runner)
    snapshot = PipeWireSnapshotProvider(timeout_s=1.25).discover()

    assert snapshot.complete
    assert calls == [
        (
            ["pw-dump", "--no-colors"],
            {
                "capture_output": True,
                "text": True,
                "timeout": 1.25,
                "input": None,
                "env": None,
                "cwd": None,
                "check": False,
            },
        )
    ]


def test_pipewire_snapshot_provider_fails_closed_if_either_parser_is_incomplete() -> None:
    # A malformed non-virtual source makes identity discovery incomplete even
    # though the unrelated route graph can still be resolved.
    payload = [
        *[USB_DEVICE, USB_SOURCE, *_graph_fixture()],
        {
            "id": 707,
            "type": "PipeWire:Interface:Node",
            "info": {"props": {"media.class": "Audio/Source", "node.name": "unknown-usb-source"}},
        },
    ]
    proc.set_runner(
        lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0, json.dumps(payload), "")
    )

    snapshot = PipeWireSnapshotProvider().discover()

    assert not snapshot.complete
    assert not snapshot.audio.complete
    assert snapshot.graph.complete


@pytest.mark.parametrize("timeout_s", [0, math.nan, math.inf])
def test_pipewire_snapshot_provider_rejects_nonpositive_or_unbounded_timeout(
    timeout_s: float,
) -> None:
    with pytest.raises(ValueError, match="timeout_s must be positive and finite"):
        PipeWireSnapshotProvider(timeout_s=timeout_s)


def test_pipewire_snapshot_provider_wraps_command_and_payload_errors() -> None:
    proc.set_runner(
        lambda argv, **kwargs: (_ for _ in ()).throw(
            subprocess.TimeoutExpired(argv, kwargs.get("timeout", 0))
        )
    )
    with pytest.raises(PipeWireDiscoveryError, match="Could not inspect audio graph"):
        PipeWireSnapshotProvider(timeout_s=0.2).discover()

    proc.set_runner(lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0, "not json", ""))
    with pytest.raises(PipeWireDiscoveryError, match="Could not parse audio graph snapshot"):
        PipeWireSnapshotProvider().discover()


def test_owned_pw_link_argv_uses_process_lifetime_and_route_ownership_tags() -> None:
    from hearth.capture_pipewire import PipeWireEndpoint, build_owned_pw_link_argv

    argv = build_owned_pw_link_argv(
        PipeWireEndpoint("vm_game", "monitor_FL", "output"),
        PipeWireEndpoint("vm_share", "playback_FL", "input"),
        route_id="route-001",
        session_id="session-001",
    )

    assert argv[:2] == ["pw-link", "--monitor"]
    assert argv[2] == "--props"
    properties = json.loads(argv[3])
    assert properties == {
        "hearth.route.id": "route-001",
        "hearth.route.kind": "capture-session",
        "hearth.route.owner": "io.github.roaring1.Hearth",
        "hearth.route.session": "session-001",
    }
    assert argv[-2:] == ["vm_game:monitor_FL", "vm_share:playback_FL"]
    assert "--linger" not in argv
    assert "--wait" not in argv


def test_owned_pw_link_argv_rejects_wrong_direction_and_ambiguous_endpoint_names() -> None:
    from hearth.capture_pipewire import PipeWireEndpoint, build_owned_pw_link_argv

    output = PipeWireEndpoint("vm_game", "monitor_FL", "input")
    input = PipeWireEndpoint("vm_share", "playback_FL", "input")
    with pytest.raises(ValueError, match="wrong direction"):
        build_owned_pw_link_argv(output, input, route_id="route-1", session_id="session-1")

    output = PipeWireEndpoint("vm:game", "monitor_FL", "output")
    with pytest.raises(ValueError, match="not safely addressable"):
        build_owned_pw_link_argv(
            output,
            PipeWireEndpoint("vm_share", "playback_FL", "input"),
            route_id="route-1",
            session_id="session-1",
        )


def test_owned_pw_link_argv_rejects_unsafe_route_metadata_tokens() -> None:
    from hearth.capture_pipewire import PipeWireEndpoint, build_owned_pw_link_argv

    with pytest.raises(ValueError, match="route ID"):
        build_owned_pw_link_argv(
            PipeWireEndpoint("vm_game", "monitor_FL", "output"),
            PipeWireEndpoint("vm_share", "playback_FL", "input"),
            route_id="session id with spaces",
            session_id="session-1",
        )
