"""Fixture-friendly PipeWire audio-source inventory and identity association.

PipeWire exposes Device and Node objects in `pw-dump`. A node's `device.id`
can join it to a Device object inside that one snapshot; the ID is only a
snapshot-local foreign key and is never exposed as a persistent selector.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from hearth import proc
from hearth.capture_devices import (
    AudioSourceCandidate,
    normalize_usb_parent_path,
)
from hearth.capture_session import UsbIdentity
from hearth.errors import CommandError, HearthError


def _props(obj: object) -> dict[str, Any]:
    if not isinstance(obj, dict):
        return {}
    info = obj.get("info")
    if not isinstance(info, dict):
        return {}
    properties = info.get("props")
    return dict(properties) if isinstance(properties, dict) else {}


def _object_id(value: object) -> int | None:
    """Normalize an ID solely for joining objects within one pw-dump snapshot."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value >= 0:
        return value
    if isinstance(value, str) and value.isdecimal():
        return int(value)
    return None


def _usb_id(value: object, label: str) -> str:
    """Normalize PipeWire's integer or udev-style string USB IDs to 4 hex digits."""
    if isinstance(value, bool):
        raise ValueError(f"PipeWire {label} is not a valid USB ID")
    if isinstance(value, int):
        number = value
    elif isinstance(value, str):
        raw = value.strip().lower()
        try:
            if raw.startswith("0x"):
                number = int(raw[2:], 16)
            elif len(raw) == 4 and all(char in "0123456789abcdef" for char in raw):
                number = int(raw, 16)
            elif raw.isdecimal():
                number = int(raw, 10)
            else:
                raise ValueError
        except ValueError as exc:
            raise ValueError(f"PipeWire {label} is not a valid USB ID") from exc
    else:
        raise ValueError(f"PipeWire {label} is not a valid USB ID")
    if not 0 <= number <= 0xFFFF:
        raise ValueError(f"PipeWire {label} is outside the USB ID range")
    return f"{number:04x}"


def _usb_identity(properties: Mapping[str, Any]) -> UsbIdentity:
    bus = properties.get("device.bus")
    if not isinstance(bus, str) or bus.lower() != "usb":
        raise ValueError("PipeWire device is not confirmed as USB")
    vendor = _usb_id(properties.get("device.vendor.id"), "vendor ID")
    product = _usb_id(properties.get("device.product.id"), "product ID")
    serial_value = properties.get("device.serial")
    serial = (
        serial_value.strip() if isinstance(serial_value, str) and serial_value.strip() else None
    )
    path_value = properties.get("device.bus-path")
    path = normalize_usb_parent_path(path_value if isinstance(path_value, str) else None)
    return UsbIdentity(vendor, product, serial_number=serial, port_path=path)


def _is_true(value: object) -> bool:
    return value is True or (isinstance(value, str) and value.casefold() in {"true", "1", "yes"})


@dataclass(frozen=True, slots=True)
class PipeWireAudioIssue:
    """A hardware source whose USB identity could not be established."""

    node_name: str
    message: str


@dataclass(frozen=True, slots=True)
class PipeWireAudioInventory:
    """Audio sources from one snapshot; `complete` gates automatic matching."""

    sources: tuple[AudioSourceCandidate, ...] = ()
    issues: tuple[PipeWireAudioIssue, ...] = ()

    @property
    def complete(self) -> bool:
        return not self.issues


@dataclass(frozen=True, slots=True)
class PipeWireGraphIssue:
    """A graph object whose stable endpoint identity could not be resolved."""

    object_type: str
    message: str


@dataclass(frozen=True, slots=True)
class PipeWireEndpoint:
    """Stable endpoint identity from node/port names, never PipeWire object IDs."""

    node_name: str
    port_name: str
    direction: str


@dataclass(frozen=True, slots=True)
class PipeWireLink:
    """One observed link; all snapshot-local numeric IDs are deliberately absent."""

    output: PipeWireEndpoint
    input: PipeWireEndpoint
    state: str
    route_properties: tuple[tuple[str, str], ...] = ()
    lingering: bool | None = None
    passive: bool | None = None

    def route_property(self, name: str) -> str | None:
        """Return a captured Hearth route marker, if one exists."""
        key = f"hearth.route.{name}"
        return next((value for prop, value in self.route_properties if prop == key), None)

    @property
    def route_owner(self) -> str | None:
        return self.route_property("owner")

    @property
    def route_kind(self) -> str | None:
        return self.route_property("kind")

    @property
    def route_id(self) -> str | None:
        return self.route_property("id")

    @property
    def hearth_owned(self) -> bool:
        """True only when owner, route kind and stable route ID are all explicit."""
        return (
            self.route_owner == "io.github.roaring1.Hearth"
            and bool(self.route_kind)
            and bool(self.route_id)
        )

    @property
    def session_owned(self) -> bool:
        """True only for a tagged session link that is not marked persistent."""
        return (
            self.hearth_owned
            and bool(self.route_property("session"))
            and self.lingering is not True
        )


@dataclass(frozen=True, slots=True)
class PipeWireGraphInventory:
    """Resolved node/port links from one snapshot; IDs never escape the parser."""

    links: tuple[PipeWireLink, ...] = ()
    issues: tuple[PipeWireGraphIssue, ...] = ()
    ports: tuple[PipeWireEndpoint, ...] = ()

    @property
    def complete(self) -> bool:
        return not self.issues


def _snapshot_objects(text: str) -> list[object]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"pw-dump returned invalid JSON: {exc.msg}") from exc
    if not isinstance(payload, list):
        raise ValueError("pw-dump root must be a JSON array")
    return payload


def _direction(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    lowered = value.strip().lower()
    lowered = {"in": "input", "out": "output"}.get(lowered, lowered)
    return lowered if lowered in {"input", "output"} else None


def _optional_link_bool(properties: Mapping[str, Any], key: str) -> bool | None:
    """Parse a known PipeWire boolean property without guessing unknown values."""
    if key not in properties:
        return None
    value = properties[key]
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in {"true", "1", "yes"}:
            return True
        if normalized in {"false", "0", "no"}:
            return False
    raise ValueError(f"PipeWire {key} property is invalid")


def _link_endpoint_ids(
    info: Mapping[str, Any], properties: Mapping[str, Any], *, side: str
) -> tuple[int, int]:
    """Read and cross-check link endpoint IDs in both pw-dump representations."""
    node_key = f"{side}-node-id"
    port_key = f"{side}-port-id"
    node_property = f"link.{side}.node"
    port_property = f"link.{side}.port"
    info_node = _object_id(info.get(node_key))
    info_port = _object_id(info.get(port_key))
    prop_node = _object_id(properties.get(node_property))
    prop_port = _object_id(properties.get(port_property))
    if info_node is None or info_port is None:
        raise ValueError("link endpoint IDs are missing or invalid")
    if (node_property in properties and prop_node != info_node) or (
        port_property in properties and prop_port != info_port
    ):
        raise ValueError("link endpoint properties disagree with link info")
    return info_node, info_port


def parse_pw_dump_graph(text: str) -> PipeWireGraphInventory:
    """Resolve PipeWire Link objects to stable ``node.name``/``port.name`` pairs.

    Numeric global IDs are used only as foreign keys inside this one JSON
    snapshot. A link is accepted only when its Link info and properties agree,
    both endpoint objects exist, each Port belongs to the referenced Node, and
    the Port directions are output-to-input. Any unresolved object makes the
    inventory incomplete so a future route verifier cannot mistake a partial
    dump for proof that a link is absent.

    Only Hearth's explicit ``hearth.route.*`` metadata is retained. Runtime
    IDs, object serials, unrelated properties and raw error details are not
    exposed by the returned value objects.
    """
    payload = _snapshot_objects(text)
    issues: list[PipeWireGraphIssue] = []
    nodes: dict[int, str] = {}
    node_names: dict[str, int] = {}
    ambiguous_node_ids: set[int] = set()
    ambiguous_node_names: set[str] = set()
    ports: dict[int, tuple[int, str, str]] = {}
    ambiguous_port_ids: set[int] = set()
    port_endpoints: set[tuple[str, str, str]] = set()
    link_objects: list[dict[str, Any]] = []

    for raw in payload:
        if not isinstance(raw, dict):
            continue
        raw_type = raw.get("type")
        object_type = raw_type if isinstance(raw_type, str) else ""
        info = raw.get("info")
        info = info if isinstance(info, dict) else {}
        properties = info.get("props")
        properties = properties if isinstance(properties, dict) else {}

        if object_type.endswith(":Node"):
            object_id = _object_id(raw.get("id"))
            raw_name = properties.get("node.name")
            node_name = raw_name.strip() if isinstance(raw_name, str) else ""
            if object_id is None or not node_name:
                issues.append(PipeWireGraphIssue("Node", "node identity is incomplete"))
                continue
            if (
                object_id in nodes
                or object_id in ambiguous_node_ids
                or node_name in node_names
                or node_name in ambiguous_node_names
            ):
                issues.append(PipeWireGraphIssue("Node", "duplicate node identity in snapshot"))
                previous_name = nodes.pop(object_id, None)
                if previous_name is not None:
                    node_names.pop(previous_name, None)
                    ambiguous_node_names.add(previous_name)
                previous_id = node_names.pop(node_name, None)
                if previous_id is not None:
                    nodes.pop(previous_id, None)
                    ambiguous_node_ids.add(previous_id)
                ambiguous_node_ids.add(object_id)
                ambiguous_node_names.add(node_name)
                continue
            nodes[object_id] = node_name
            node_names[node_name] = object_id

        elif object_type.endswith(":Port"):
            object_id = _object_id(raw.get("id"))
            node_id = _object_id(properties.get("node.id"))
            raw_name = properties.get("port.name")
            port_name = raw_name.strip() if isinstance(raw_name, str) else ""
            direction = _direction(info.get("direction"))
            prop_direction = properties.get("port.direction")
            if isinstance(prop_direction, str):
                prop_direction = {"in": "input", "out": "output"}.get(
                    prop_direction.strip().lower(), prop_direction.strip().lower()
                )
            if (
                object_id is None
                or node_id is None
                or not port_name
                or direction is None
                or (prop_direction is not None and prop_direction != direction)
            ):
                issues.append(
                    PipeWireGraphIssue("Port", "port identity or direction is incomplete")
                )
                continue
            if object_id in ports or object_id in ambiguous_port_ids:
                issues.append(PipeWireGraphIssue("Port", "duplicate port identity in snapshot"))
                ports.pop(object_id, None)
                ambiguous_port_ids.add(object_id)
                continue
            if node_id not in nodes:
                # A pw-dump snapshot is an unordered object list, so resolve
                # node references in the second pass rather than failing here.
                pass
            ports[object_id] = (node_id, port_name, direction)

        elif object_type.endswith(":Link"):
            link_objects.append({"info": info, "properties": properties})

    links: list[PipeWireLink] = []
    for item in link_objects:
        info = item["info"]
        properties = item["properties"]
        try:
            output_node_id, output_port_id = _link_endpoint_ids(info, properties, side="output")
            input_node_id, input_port_id = _link_endpoint_ids(info, properties, side="input")
            output_node = nodes.get(output_node_id)
            input_node = nodes.get(input_node_id)
            output_port = ports.get(output_port_id)
            input_port = ports.get(input_port_id)
            if (
                output_node is None
                or input_node is None
                or output_node_id in ambiguous_node_ids
                or input_node_id in ambiguous_node_ids
                or output_node in ambiguous_node_names
                or input_node in ambiguous_node_names
            ):
                raise ValueError("link references an unknown or ambiguous node")
            if (
                output_port is None
                or input_port is None
                or output_port_id in ambiguous_port_ids
                or input_port_id in ambiguous_port_ids
            ):
                raise ValueError("link references an unknown or ambiguous port")
            if output_port[0] != output_node_id or input_port[0] != input_node_id:
                raise ValueError("link port belongs to a different node")
            if output_port[2] != "output" or input_port[2] != "input":
                raise ValueError("link port directions are not output-to-input")
            state_value = info.get("state")
            state = state_value.strip().lower() if isinstance(state_value, str) else ""
            if not state:
                raise ValueError("link state is missing")

            route_properties: list[tuple[str, str]] = []
            for key, value in properties.items():
                if not isinstance(key, str) or not key.startswith("hearth.route."):
                    continue
                if not isinstance(value, str) or not value.strip():
                    raise ValueError("Hearth route metadata is invalid")
                route_properties.append((key, value.strip()))
            links.append(
                PipeWireLink(
                    output=PipeWireEndpoint(output_node, output_port[1], "output"),
                    input=PipeWireEndpoint(input_node, input_port[1], "input"),
                    state=state,
                    route_properties=tuple(sorted(route_properties)),
                    lingering=_optional_link_bool(properties, "object.linger"),
                    passive=_optional_link_bool(properties, "link.passive"),
                )
            )
        except ValueError as exc:
            issues.append(PipeWireGraphIssue("Link", str(exc)))

    # Stable names must be unambiguous within their direction on a node.
    resolved_ports: list[PipeWireEndpoint] = []
    for node_id, port_name, direction in ports.values():
        resolved_node_name = nodes.get(node_id)
        if resolved_node_name is None:
            issues.append(PipeWireGraphIssue("Port", "port references an unknown node"))
            continue
        endpoint = (resolved_node_name, port_name, direction)
        if endpoint in port_endpoints:
            issues.append(PipeWireGraphIssue("Port", "duplicate stable port endpoint in snapshot"))
        else:
            port_endpoints.add(endpoint)
            resolved_ports.append(PipeWireEndpoint(resolved_node_name, port_name, direction))

    return PipeWireGraphInventory(
        links=tuple(links),
        issues=tuple(issues),
        ports=tuple(resolved_ports),
    )


def build_owned_pw_link_argv(
    output: PipeWireEndpoint,
    input: PipeWireEndpoint,
    *,
    route_id: str,
    session_id: str,
    kind: str = "capture-session",
    executable: str = "pw-link",
) -> list[str]:
    """Build (but never execute) a process-owned, non-lingering PipeWire link.

    `--monitor` deliberately suppresses PipeWire's default `object.linger=true`
    property and keeps pw-link alive while it owns the link. The caller must
    spawn this argv with `proc.spawn_owned()` and verify the exact tagged link
    in a fresh `PipeWireSnapshot` before considering route acquisition ready.
    No `--wait` is used: endpoints disappearing after preflight should fail
    startup/rollback rather than leave a child waiting indefinitely.
    """
    if not isinstance(executable, str) or not executable.strip() or "\x00" in executable:
        raise ValueError("pw-link executable must be a non-empty executable name")

    def endpoint_spec(endpoint: PipeWireEndpoint, expected: str) -> str:
        if endpoint.direction != expected:
            raise ValueError(f"pw-link {expected} endpoint has the wrong direction")
        values = (endpoint.node_name, endpoint.port_name)
        if any(
            not value
            or value != value.strip()
            or ":" in value
            or any(ord(char) < 32 or ord(char) == 127 for char in value)
            for value in values
        ):
            raise ValueError("pw-link endpoint names are empty or not safely addressable")
        return f"{endpoint.node_name}:{endpoint.port_name}"

    def metadata_token(value: str, label: str) -> str:
        if (
            not isinstance(value, str)
            or not value
            or len(value) > 128
            or not value[0].isalnum()
            or any(not (char.isalnum() or char in "._-") for char in value)
        ):
            raise ValueError(f"{label} must be a short stable token")
        return value

    properties = {
        "hearth.route.owner": "io.github.roaring1.Hearth",
        "hearth.route.kind": metadata_token(kind, "route kind"),
        "hearth.route.id": metadata_token(route_id, "route ID"),
        "hearth.route.session": metadata_token(session_id, "session ID"),
    }
    return [
        executable.strip(),
        "--monitor",
        "--props",
        json.dumps(properties, sort_keys=True, separators=(",", ":")),
        endpoint_spec(output, "output"),
        endpoint_spec(input, "input"),
    ]


def parse_pw_dump_audio_inventory(text: str) -> PipeWireAudioInventory:
    """Parse physical USB Audio/Source nodes from one `pw-dump` JSON snapshot.

    Non-USB and explicitly virtual sources are not capture-card candidates.
    An apparent hardware source with missing/ambiguous device identity is kept
    as an issue, making the snapshot incomplete so callers cannot auto-select
    a different endpoint based on a partial inventory.
    """
    payload = _snapshot_objects(text)

    devices: dict[int, dict[str, Any]] = {}
    nodes: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for obj in payload:
        if not isinstance(obj, dict):
            continue
        object_type = obj.get("type")
        object_type = object_type if isinstance(object_type, str) else ""
        properties = _props(obj)
        if object_type.endswith(":Device"):
            object_id = _object_id(obj.get("id"))
            if object_id is not None:
                devices[object_id] = properties
        elif object_type.endswith(":Node"):
            nodes.append((obj, properties))

    sources: list[AudioSourceCandidate] = []
    issues: list[PipeWireAudioIssue] = []
    for _node, node_properties in nodes:
        if node_properties.get("media.class") != "Audio/Source":
            continue
        if _is_true(node_properties.get("node.virtual")):
            continue

        raw_name = node_properties.get("node.name")
        node_name = raw_name.strip() if isinstance(raw_name, str) else ""
        reference = _object_id(node_properties.get("device.id"))
        device_properties = devices.get(reference, {}) if reference is not None else {}
        properties = {**device_properties, **node_properties}

        bus = properties.get("device.bus")
        if (
            bus is None
            and reference is None
            and not any(key.startswith("device.") for key in node_properties)
        ):
            issues.append(
                PipeWireAudioIssue(
                    node_name or "unnamed audio source",
                    "Audio source has no device identity metadata",
                )
            )
            continue
        if isinstance(bus, str) and bus.lower() != "usb":
            continue

        try:
            identity = _usb_identity(properties)
            if not node_name:
                raise ValueError("PipeWire source has no stable node.name selector")
            display = next(
                (
                    value.strip()
                    for key in ("node.description", "node.nick", "device.description")
                    if isinstance((value := properties.get(key)), str) and value.strip()
                ),
                node_name,
            )
            sources.append(AudioSourceCandidate(node_name, display, identity))
        except ValueError as exc:
            issues.append(PipeWireAudioIssue(node_name or "unnamed audio source", str(exc)))

    return PipeWireAudioInventory(tuple(sources), tuple(issues))


class PipeWireDiscoveryError(HearthError):
    """The one-shot PipeWire inventory command could not be completed."""


@dataclass(frozen=True, slots=True)
class PipeWireSnapshot:
    """Audio identity and link ownership resolved from the same graph snapshot."""

    audio: PipeWireAudioInventory
    graph: PipeWireGraphInventory

    @property
    def complete(self) -> bool:
        return self.audio.complete and self.graph.complete


def parse_pw_dump_snapshot(text: str) -> PipeWireSnapshot:
    """Parse audio candidates and route links from the exact same pw-dump text."""
    return PipeWireSnapshot(
        audio=parse_pw_dump_audio_inventory(text),
        graph=parse_pw_dump_graph(text),
    )


class PipeWireSnapshotProvider:
    """Read one bounded PipeWire dump for both identity and route verification."""

    def __init__(self, *, timeout_s: float = 3.0) -> None:
        if not math.isfinite(timeout_s) or timeout_s <= 0:
            raise ValueError("timeout_s must be positive and finite")
        self._timeout_s = timeout_s

    def discover(self) -> PipeWireSnapshot:
        try:
            result = proc.run(["pw-dump", "--no-colors"], timeout=self._timeout_s)
        except CommandError as exc:
            raise PipeWireDiscoveryError(f"Could not inspect audio graph: {exc}") from exc
        try:
            return parse_pw_dump_snapshot(result.stdout)
        except ValueError as exc:
            raise PipeWireDiscoveryError(f"Could not parse audio graph snapshot: {exc}") from exc


class PipeWireLinkLeaseError(HearthError):
    """A process-owned link could not be acquired, verified or released safely."""


class OwnedPipeWireLinkProcess(Protocol):
    """Small process boundary implemented by `proc.OwnedProcess` and test fakes."""

    def poll(self) -> int | None: ...

    def stop(self, *, timeout: float = 1.0) -> int: ...


class PipeWireSnapshotSource(Protocol):
    """One-shot graph snapshot source used for post-create/post-stop verification."""

    def discover(self) -> PipeWireSnapshot: ...


class PipeWireProcessLinkLease:
    """Own one non-lingering link through the lifetime of its pw-link child.

    The caller is responsible for checking the endpoints are safe/unused before
    spawning the argv from `build_owned_pw_link_argv()` and then passing that
    `proc.spawn_owned()` handle here. This object never starts a command. Every
    verification uses a new bounded snapshot; an incomplete graph is never
    treated as proof that a link is present or absent.
    """

    def __init__(
        self,
        process: OwnedPipeWireLinkProcess,
        snapshots: PipeWireSnapshotSource,
        *,
        output: PipeWireEndpoint,
        input: PipeWireEndpoint,
        route_id: str,
        session_id: str,
        kind: str = "capture-session",
    ) -> None:
        # Reuse the argv builder's validation without retaining or executing
        # its command. This keeps ownership tokens and directions consistent.
        build_owned_pw_link_argv(
            output,
            input,
            route_id=route_id,
            session_id=session_id,
            kind=kind,
        )
        self._process = process
        self._snapshots = snapshots
        self._output = output
        self._input = input
        self._route_id = route_id
        self._session_id = session_id
        self._kind = kind
        self._stopped = False
        self._released = False

    def _route_links(self, graph: PipeWireGraphInventory) -> tuple[PipeWireLink, ...]:
        return tuple(
            link
            for link in graph.links
            if link.route_owner == "io.github.roaring1.Hearth"
            and link.route_id == self._route_id
            and link.route_property("session") == self._session_id
        )

    def verify_acquired(self) -> PipeWireLink:
        """Require one active, correctly tagged link while the owner child lives."""
        if self._released or self._stopped or self._process.poll() is not None:
            raise PipeWireLinkLeaseError("owned pw-link process is not running")
        snapshot = self._snapshots.discover()
        if not snapshot.graph.complete:
            raise PipeWireLinkLeaseError("PipeWire graph snapshot is incomplete during link verify")
        matches = self._route_links(snapshot.graph)
        if len(matches) != 1:
            raise PipeWireLinkLeaseError(
                f"expected one session-owned PipeWire link, found {len(matches)}"
            )
        link = matches[0]
        if (
            not link.session_owned
            or link.route_kind != self._kind
            or link.output != self._output
            or link.input != self._input
            or link.state != "active"
            or link.passive is True
        ):
            raise PipeWireLinkLeaseError(
                "PipeWire link endpoints, state, ownership or lifetime do not match the session"
            )
        return link

    def release(self, *, timeout: float = 1.0) -> None:
        """Stop/reap the process owner, then verify its tagged link disappeared."""
        if self._released:
            return
        if not self._stopped:
            self._process.stop(timeout=timeout)
            self._stopped = True
        snapshot = self._snapshots.discover()
        if not snapshot.graph.complete:
            raise PipeWireLinkLeaseError("PipeWire graph snapshot is incomplete during cleanup")
        remaining = self._route_links(snapshot.graph)
        if remaining:
            raise PipeWireLinkLeaseError(
                f"{len(remaining)} tagged session PipeWire link(s) remain after owner exit"
            )
        self._released = True


class PipeWireAudioProvider:
    """Read one bounded PipeWire snapshot on explicit request; no monitoring."""

    def __init__(self, *, timeout_s: float = 3.0) -> None:
        if not math.isfinite(timeout_s) or timeout_s <= 0:
            raise ValueError("timeout_s must be positive and finite")
        self._timeout_s = timeout_s

    def discover(self) -> PipeWireAudioInventory:
        try:
            result = proc.run(["pw-dump", "--no-colors"], timeout=self._timeout_s)
        except CommandError as exc:
            raise PipeWireDiscoveryError(f"Could not inspect audio devices: {exc}") from exc
        try:
            return parse_pw_dump_audio_inventory(result.stdout)
        except ValueError as exc:
            raise PipeWireDiscoveryError(f"Could not parse audio device inventory: {exc}") from exc
