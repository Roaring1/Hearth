"""Fixture-friendly parsing and selection for UVC/V4L2 capture devices.

This module consumes the stdout from read-only ``v4l2-ctl`` and udev probes;
it never runs a process or opens a device. The runtime provider is responsible
for invoking bounded commands through ``hearth.proc`` and passing their output
here. Unknown or ambiguous hardware is not guessed into a usable capture node.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from fractions import Fraction

from hearth.capture_session import UsbIdentity, VideoMode

_VIDEO_NODE = re.compile(r"^/dev/video\d+$")
_FORMAT = re.compile(r"^\s*\[\d+\]:\s+'(?P<fourcc>.{4})'\s+")
_SIZE = re.compile(r"^\s*Size:\s+Discrete\s+(?P<width>\d+)x(?P<height>\d+)\s*$")
_FPS = re.compile(r"\((?P<fps>\d+(?:\.\d+)?)\s+fps\)")


@dataclass(frozen=True, slots=True)
class UdevVideoProperties:
    """Stable USB parent identity and product label for one V4L2 node."""

    device_path: str
    identity: UsbIdentity
    product_name: str
    v4l_capabilities: frozenset[str]


class AudioAssociationStatus(StrEnum):
    """What USB-parent evidence says about the capture card's audio source."""

    UNKNOWN = "unknown"
    NONE = "none"
    MATCHED = "matched"
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True, slots=True)
class AudioSourceCandidate:
    """One discovered USB-backed audio source with an adapter-supplied selector.

    ``source_selector`` must be a stable node property chosen by the future
    PipeWire adapter, never a numeric object ID or ALSA card number.
    """

    source_selector: str
    display_name: str
    identity: UsbIdentity

    def __post_init__(self) -> None:
        selector = self.source_selector.strip()
        if not selector:
            raise ValueError("audio source selector must not be blank")
        if selector.isdecimal() or re.fullmatch(
            r"(?:card|alsa_card|node[._-]?id)[=:]?\d+", selector, re.IGNORECASE
        ):
            raise ValueError("audio selector must not be a volatile numeric device/object ID")


@dataclass(frozen=True, slots=True)
class AudioAssociation:
    """A conservative association result; `selected` exists only when unique."""

    status: AudioAssociationStatus
    candidates: tuple[AudioSourceCandidate, ...] = ()

    @property
    def selected(self) -> AudioSourceCandidate | None:
        return self.candidates[0] if self.status is AudioAssociationStatus.MATCHED else None


@dataclass(frozen=True, slots=True)
class V4L2Capabilities:
    """Device Caps values read from ``v4l2-ctl --all``."""

    video_capture: bool
    video_capture_multiplanar: bool
    metadata_capture: bool

    @property
    def capture_capable(self) -> bool:
        return self.video_capture or self.video_capture_multiplanar


@dataclass(frozen=True, slots=True)
class CaptureNode:
    """One V4L2 node tied to its stable USB device and discrete capabilities."""

    device_path: str
    card_label: str
    identity: UsbIdentity
    capabilities: V4L2Capabilities
    modes: tuple[VideoMode, ...]

    @property
    def eligible(self) -> bool:
        """A real video capture node with at least one enumerated discrete mode."""
        return self.capabilities.capture_capable and bool(self.modes)


@dataclass(frozen=True, slots=True)
class CaptureDevice:
    """All discovered V4L2 nodes belonging to one stable USB identity."""

    identity: UsbIdentity
    nodes: tuple[CaptureNode, ...]

    @property
    def capture_nodes(self) -> tuple[CaptureNode, ...]:
        return tuple(node for node in self.nodes if node.eligible)


def parse_video_device_listing(text: str) -> dict[str, str]:
    """Parse ``v4l2-ctl --list-devices`` into node path -> card label.

    Metadata and media nodes are deliberately omitted here; the per-node
    capability probe decides which ``/dev/videoN`` entries can capture frames.
    """
    devices: dict[str, str] = {}
    current_label = ""
    for line in text.splitlines():
        if not line.strip():
            continue
        if not line[0].isspace():
            current_label = line.strip().rstrip(":")
            continue
        path = line.strip()
        if _VIDEO_NODE.fullmatch(path) and current_label:
            devices[path] = current_label
    return devices


def _udev_properties(text: str) -> dict[str, str]:
    properties: dict[str, str] = {}
    for line in text.splitlines():
        key, separator, value = line.partition("=")
        if separator and key and key not in properties:
            properties[key] = value
    return properties


def normalize_usb_parent_path(value: str | None) -> str | None:
    """Remove a udev USB interface suffix while preserving physical topology.

    Typical composite-device ID_PATH values end in ``:configuration.interface``
    (for example ``...-usb-0:5:1.2``). Video and audio interfaces then share
    the parent ``...-usb-0:5``. Values without that suffix are kept unchanged.
    """
    if value is None:
        return None
    path = value.strip()
    if not path:
        return None
    return re.sub(r":\d+\.\d+$", "", path)


def parse_udev_usb_identity(text: str) -> UsbIdentity:
    """Parse a stable identity from USB-backed video or audio udev properties."""
    properties = _udev_properties(text)
    bus = properties.get("ID_BUS", "").lower()
    if bus != "usb":
        raise ValueError("device is not confirmed as USB-backed by udev")
    vendor = properties.get("ID_VENDOR_ID", "")
    product = properties.get("ID_MODEL_ID", "")
    serial = properties.get("ID_SERIAL_SHORT") or None
    port_path = normalize_usb_parent_path(properties.get("ID_PATH"))
    return UsbIdentity(vendor, product, serial_number=serial, port_path=port_path)


def same_usb_device(left: UsbIdentity, right: UsbIdentity) -> bool:
    """Compare serial first; fall back to matching USB parent path if needed."""
    if (left.vendor_id.lower(), left.product_id.lower()) != (
        right.vendor_id.lower(),
        right.product_id.lower(),
    ):
        return False
    if left.serial_number and right.serial_number:
        return left.serial_number == right.serial_number
    return bool(left.port_path and right.port_path and left.port_path == right.port_path)


def associate_audio_source(
    video_device: CaptureDevice,
    audio_sources: Iterable[AudioSourceCandidate] | None,
) -> AudioAssociation:
    """Match by shared USB identity only; no name/card-number heuristic.

    ``None`` means the audio inventory could not be queried, which is distinct
    from a successful query returning no matching source. Multiple matches
    remain ambiguous and are returned for an explicit user choice.
    """
    if audio_sources is None:
        return AudioAssociation(AudioAssociationStatus.UNKNOWN)
    matches = tuple(
        source
        for source in audio_sources
        if same_usb_device(video_device.identity, source.identity)
    )
    if not matches:
        return AudioAssociation(AudioAssociationStatus.NONE)
    if len(matches) > 1:
        return AudioAssociation(AudioAssociationStatus.AMBIGUOUS, matches)
    return AudioAssociation(AudioAssociationStatus.MATCHED, matches)


def parse_udev_video_properties(text: str) -> UdevVideoProperties:
    """Parse ``udevadm info --query=property`` for one V4L2 node."""
    properties = _udev_properties(text)
    device_path = properties.get("DEVNAME", "")
    if not _VIDEO_NODE.fullmatch(device_path):
        raise ValueError("udev data does not identify a /dev/videoN node")
    identity = parse_udev_usb_identity(text)
    raw_caps = properties.get("ID_V4L_CAPABILITIES", "")
    capabilities = frozenset(part for part in raw_caps.strip(":").split(":") if part)
    return UdevVideoProperties(
        device_path=device_path,
        identity=identity,
        product_name=properties.get("ID_V4L_PRODUCT")
        or properties.get("ID_MODEL_FROM_DATABASE")
        or properties.get("ID_MODEL")
        or "USB video device",
        v4l_capabilities=capabilities,
    )


def parse_v4l2_capabilities(text: str) -> V4L2Capabilities:
    """Parse the textual capability names from ``v4l2-ctl --all``.

    Prefer the ``Device Caps`` block when present; the aggregate Capabilities
    block may include features supplied by other nodes on the same card.
    """
    lines = text.splitlines()
    marker = next(
        (index for index, line in enumerate(lines) if line.strip().startswith("Device Caps")),
        None,
    )
    if marker is None:
        raise ValueError(
            "v4l2-ctl output has no per-node Device Caps block; "
            "aggregate Capabilities are insufficient"
        )

    found: set[str] = set()
    for line in lines[marker + 1 :]:
        if line and not line[0].isspace():
            break
        name = line.strip()
        if name in {"Video Capture", "Video Capture Multiplanar", "Metadata Capture"}:
            found.add(name)
    return V4L2Capabilities(
        video_capture="Video Capture" in found,
        video_capture_multiplanar="Video Capture Multiplanar" in found,
        metadata_capture="Metadata Capture" in found,
    )


def parse_v4l2_discrete_modes(text: str) -> tuple[VideoMode, ...]:
    """Parse only discrete formats, sizes and frame intervals from V4L2 output.

    Stepwise/continuous ranges are intentionally not expanded or guessed. The
    UI may offer only discrete combinations actually printed by the driver.
    """
    current_format: str | None = None
    current_size: tuple[int, int] | None = None
    modes: set[VideoMode] = set()
    for line in text.splitlines():
        format_match = _FORMAT.match(line)
        if format_match:
            current_format = format_match.group("fourcc")
            current_size = None
            continue
        if line.lstrip().startswith("Size:"):
            # A stepwise/continuous size invalidates the previous discrete size.
            # Never attach its frame intervals to a stale discrete resolution.
            current_size = None
            size_match = _SIZE.match(line)
            if size_match and current_format is not None:
                current_size = (int(size_match.group("width")), int(size_match.group("height")))
            continue
        if "Interval: Discrete" not in line or current_format is None or current_size is None:
            continue
        fps_match = _FPS.search(line)
        if fps_match is None:
            continue
        try:
            fps = Fraction(Decimal(fps_match.group("fps"))).limit_denominator(1_000_000)
        except (InvalidOperation, ZeroDivisionError):
            continue
        if fps <= 0:
            continue
        modes.add(
            VideoMode(
                pixel_format=current_format,
                width=current_size[0],
                height=current_size[1],
                fps_numerator=fps.numerator,
                fps_denominator=fps.denominator,
            )
        )
    return tuple(
        sorted(
            modes,
            key=lambda mode: (
                mode.width * mode.height,
                Fraction(mode.fps_numerator, mode.fps_denominator),
                mode.pixel_format,
            ),
        )
    )


def make_capture_node(
    device_path: str,
    card_label: str,
    udev: UdevVideoProperties,
    capabilities: V4L2Capabilities,
    formats_text: str,
) -> CaptureNode:
    """Join per-node udev, capability and discrete-mode probe results."""
    if device_path != udev.device_path:
        raise ValueError("udev identity does not match the requested V4L2 node")
    modes = parse_v4l2_discrete_modes(formats_text) if capabilities.capture_capable else ()
    return CaptureNode(device_path, card_label, udev.identity, capabilities, modes)


def group_capture_devices(nodes: Iterable[CaptureNode]) -> tuple[CaptureDevice, ...]:
    """Group every node from the same USB device without selecting a node."""
    grouped: dict[str, list[CaptureNode]] = {}
    identities: dict[str, UsbIdentity] = {}
    for node in nodes:
        key = node.identity.stable_key
        grouped.setdefault(key, []).append(node)
        identities[key] = node.identity
    return tuple(
        CaptureDevice(identities[key], tuple(sorted(group, key=lambda node: node.device_path)))
        for key, group in sorted(grouped.items())
    )


def choose_capture_node(device: CaptureDevice, *, device_path: str | None = None) -> CaptureNode:
    """Return one unambiguous node or fail closed; never guess among multiple."""
    candidates = device.capture_nodes
    if device_path is not None:
        selected = next((node for node in candidates if node.device_path == device_path), None)
        if selected is None:
            raise ValueError("selected node is not an eligible video capture node")
        return selected
    if not candidates:
        raise ValueError("USB device has no eligible video capture node with discrete modes")
    if len(candidates) > 1:
        paths = ", ".join(node.device_path for node in candidates)
        raise ValueError(
            f"USB device exposes multiple capture nodes; user selection required: {paths}"
        )
    return candidates[0]
