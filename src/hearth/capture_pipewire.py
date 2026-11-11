"""Fixture-friendly PipeWire audio-source inventory and identity association.

PipeWire exposes Device and Node objects in `pw-dump`. A node's `device.id`
can join it to a Device object inside that one snapshot; the ID is only a
snapshot-local foreign key and is never exposed as a persistent selector.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

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


def parse_pw_dump_audio_inventory(text: str) -> PipeWireAudioInventory:
    """Parse physical USB Audio/Source nodes from one `pw-dump` JSON snapshot.

    Non-USB and explicitly virtual sources are not capture-card candidates.
    An apparent hardware source with missing/ambiguous device identity is kept
    as an issue, making the snapshot incomplete so callers cannot auto-select
    a different endpoint based on a partial inventory.
    """
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"pw-dump returned invalid JSON: {exc.msg}") from exc
    if not isinstance(payload, list):
        raise ValueError("pw-dump root must be a JSON array")

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


class PipeWireAudioProvider:
    """Read one bounded PipeWire snapshot on explicit request; no monitoring."""

    def __init__(self, *, timeout_s: float = 3.0) -> None:
        if timeout_s <= 0:
            raise ValueError("timeout_s must be positive")
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
