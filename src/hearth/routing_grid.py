"""Pure, bounded model for Hearth's read-only audio routing grid.

The builder consumes collector data only. It never queries PipeWire, PulseAudio,
ALSA, systemd, configuration files, or GTK.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from hearth.audio.route_health import BusRouteHealth
from hearth.collector import (
    CaptureClientSnapshot,
    GraphSnapshot,
    RouteIntentSnapshot,
    SinkState,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence


class Evidence(StrEnum):
    LIVE = "live"
    OBSERVED = "observed"
    CONFIGURED = "configured"
    CAPTURE = "capture"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"
    STALE = "stale"


class Group(StrEnum):
    INPUTS = "inputs"
    PROCESSING = "processing"
    BUSES = "buses"
    DESTINATIONS = "destinations"


@dataclass(frozen=True, slots=True)
class GridNode:
    id: str
    label: str
    group: Group
    evidence: Evidence
    detail: str = ""
    ports: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class GridEdge:
    source: str
    target: str
    evidence: Evidence
    source_port: str = ""
    target_port: str = ""
    detail: str = ""


@dataclass(frozen=True, slots=True)
class RouteHealthNote:
    bus: str
    sink: str
    state: str
    verified_links: int
    expected_links: int
    detail: str = ""


@dataclass(frozen=True, slots=True)
class GridModel:
    nodes: tuple[GridNode, ...] = ()
    edges: tuple[GridEdge, ...] = ()
    status: str = "unknown"
    detail: str = ""
    taken_at: float = 0.0
    truncated: bool = False
    consumer_status: str = "unknown"
    consumer_detail: str = "Microphone consumers have not been enumerated"
    consumer_count: int = 0
    microphone_intent_status: str = "unknown"
    vesktop_status: str = "unknown"
    vesktop_targets: tuple[str, ...] = ()
    route_health: tuple[RouteHealthNote, ...] = ()

    @property
    def fingerprint(self) -> tuple[object, ...]:
        """Content identity for suppressing redraws on unchanged snapshots."""
        return (
            self.nodes,
            self.edges,
            self.status,
            self.detail,
            self.truncated,
            self.consumer_status,
            self.consumer_detail,
            self.consumer_count,
            self.microphone_intent_status,
            self.vesktop_status,
            self.vesktop_targets,
            self.route_health,
        )


MAX_GRID_NODES = 256
MAX_GRID_EDGES = 512
MAX_ROUTE_NOTES = 32
GRAPH_STALE_AFTER_S = 5.0

_BUS_LABELS = {
    "vm_game": "Game",
    "vm_chat": "Chat",
    "vm_music": "Music",
    "vm_laptop": "Laptop",
    "vm_share": "Screen Share",
    "mic_b1": "Mic B1",
    "mic_b2": "Mic B2",
}
_INPUT_LABELS = {
    "sm7b_mono": "SM7B microphone",
    "astro_mic_48k": "Astro microphone",
}
_PROCESSING_LABELS = {
    "Carla": "Carla",
}


def _node_id(port: str) -> str:
    """Return the PipeWire node name from a node:port identifier."""
    return port.rpartition(":")[0]


def _classify(
    name: str,
    *,
    playback_names: set[str],
    sink_names: set[str],
    source_names: set[str],
) -> tuple[Group, str, str]:
    lowered = name.lower()
    if name in _BUS_LABELS:
        return Group.BUSES, _BUS_LABELS[name], ""
    if name in _INPUT_LABELS:
        return Group.INPUTS, _INPUT_LABELS[name], ""
    if (
        name in _PROCESSING_LABELS
        or lowered.startswith(("input.loopback-", "output.loopback-"))
        or "carla" in lowered
        or "filter" in lowered
    ):
        return Group.PROCESSING, _PROCESSING_LABELS.get(name, name), ""
    if name.startswith(("alsa_input.", "bluez_input.")):
        return Group.INPUTS, name, "Physical or virtual capture endpoint"
    if name in playback_names:
        return Group.INPUTS, name, "Application playback source; sink-input inventory"
    if name.startswith(("alsa_output.", "bluez_output.")):
        return Group.DESTINATIONS, name, "Physical playback endpoint"
    if name.startswith("capture-client-"):
        return (
            Group.DESTINATIONS,
            name.replace("capture-client-", "Capture client #"),
            ("Observed source-output client; application name was not queried"),
        )
    if name in sink_names:
        return Group.DESTINATIONS, name, "Sink endpoint observed by the mixer collector"
    if name in source_names:
        return Group.INPUTS, name, "Source endpoint observed by the mixer collector"
    return Group.PROCESSING, name, "Unclassified node; role is not inferred"


def _route_notes(
    route_health: Mapping[str, Mapping[str, BusRouteHealth]] | None,
) -> tuple[RouteHealthNote, ...]:
    if not route_health:
        return ()
    notes: list[RouteHealthNote] = []
    for source, sinks in sorted(route_health.items()):
        bus = source.removesuffix(".monitor")
        for sink, health in sorted(sinks.items()):
            notes.append(
                RouteHealthNote(
                    bus=bus,
                    sink=sink,
                    state=health.state.value,
                    verified_links=health.verified_links,
                    expected_links=health.expected_links,
                    detail=health.detail,
                )
            )
            if len(notes) >= MAX_ROUTE_NOTES:
                return tuple(notes)
    return tuple(notes)


def build_model(
    graph: GraphSnapshot | None,
    *,
    capture_clients: CaptureClientSnapshot | None = None,
    route_intent: RouteIntentSnapshot | None = None,
    route_health: Mapping[str, Mapping[str, BusRouteHealth]] | None = None,
    sinks: Mapping[str, SinkState] | None = None,
    sources: Mapping[str, str] | None = None,
    sink_inputs: Mapping[str, Sequence[object]] | None = None,
    now: float | None = None,
) -> GridModel:
    """Build a deterministic view model from already-collected evidence."""
    sink_inventory = sinks or {}
    source_inventory = sources or {}
    streams = sink_inputs or {}
    playback_names = {
        str(getattr(stream, "name", ""))
        for group in streams.values()
        for stream in group
        if getattr(stream, "name", "")
    }
    sink_names = set(sink_inventory)
    source_names = set(source_inventory)

    graph_status = graph.status if graph is not None else "unknown"
    graph_detail = graph.detail if graph is not None else "No PipeWire graph snapshot"
    graph_taken_at = graph.taken_at if graph is not None else 0.0
    graph_ports = set(graph.ports) if graph is not None else set()
    graph_edges = set(graph.edges) if graph is not None else set()
    stale_age = max(0.0, (now - graph_taken_at)) if now is not None and graph_taken_at > 0 else 0.0
    stale = graph_status == "verified" and stale_age > GRAPH_STALE_AFTER_S
    if stale:
        graph_status = "stale"
        graph_detail = "PipeWire snapshot is stale; edges are last-known observations"

    live_nodes = {_node_id(port) for port in graph_ports}
    configured = set(route_intent.microphone_edges) if route_intent else set()
    live_node_pairs = {(_node_id(source), _node_id(target)) for source, target in graph_edges}

    names = set(live_nodes) | sink_names | source_names | playback_names
    for source, target in configured:
        names.add(source)
        names.add(target)

    capture_status = capture_clients.status if capture_clients else "unknown"
    capture_detail = (
        capture_clients.detail
        if capture_clients
        else "Microphone consumers have not been enumerated"
    )
    capture_edges: list[GridEdge] = []
    capture_node_ids: set[str] = set()
    if capture_clients is not None and capture_clients.status != "unknown":
        for item in capture_clients.clients:
            client_node = f"capture-client-{item.client}"
            capture_node_ids.add(client_node)
            names.add(client_node)
            if item.source:
                names.add(item.source)
                capture_edges.append(
                    GridEdge(
                        source=item.source,
                        target=client_node,
                        evidence=Evidence.CAPTURE,
                        source_port=item.source,
                        target_port=f"client {item.client}",
                        detail=f"Observed source-output #{item.index}; consumer name unavailable",
                    )
                )

    configured_edges: list[GridEdge] = []
    for source, target in sorted(configured):
        if (source, target) in live_node_pairs:
            continue
        source_available = source in live_nodes or source in sink_names or source in source_names
        target_available = target in live_nodes or target in sink_names or target in source_names
        if (
            graph_status == "verified"
            and graph is not None
            and graph.status == "verified"
            and (not source_available or not target_available)
        ):
            evidence = Evidence.UNAVAILABLE
            detail = "Configured endpoint absent from a complete successful inventory"
        else:
            evidence = Evidence.CONFIGURED
            detail = "Saved microphone route intent; not verified as a live edge"
        configured_edges.append(GridEdge(source, target, evidence, detail=detail))

    degraded_loopbacks: set[str] = set()
    if route_health:
        for bus_source, sinks_for_bus in route_health.items():
            if not any(
                getattr(health.state, "value", health.state) == "degraded"
                for health in sinks_for_bus.values()
            ):
                continue
            bus_name = bus_source.removesuffix(".monitor")
            for source_port, target_port in graph_edges:
                if _node_id(source_port) == bus_name and _node_id(target_port).startswith(
                    "input.loopback-"
                ):
                    degraded_loopbacks.add(_node_id(target_port).removeprefix("input.loopback-"))
    all_edges: list[GridEdge] = []
    for source, target in sorted(graph_edges):
        source_node = _node_id(source)
        target_node = _node_id(target)
        loopback_ids = {
            node.removeprefix("input.loopback-")
            for node in (source_node, target_node)
            if node.startswith("input.loopback-")
        }
        loopback_ids.update(
            node.removeprefix("output.loopback-")
            for node in (source_node, target_node)
            if node.startswith("output.loopback-")
        )
        degraded = bool(loopback_ids & degraded_loopbacks)
        evidence = Evidence.STALE if stale else Evidence.DEGRADED if degraded else Evidence.LIVE
        all_edges.append(
            GridEdge(
                source=source_node,
                target=target_node,
                evidence=evidence,
                source_port=source,
                target_port=target,
                detail=(
                    "Observed live edge in a partially verified four-link route"
                    if degraded
                    else "Observed PipeWire port-to-port edge"
                ),
            )
        )
    all_edges.extend(configured_edges)
    all_edges.extend(capture_edges)

    notes = _route_notes(route_health)
    truncated = graph_status == "truncated"
    if len(names) > MAX_GRID_NODES:
        truncated = True
    if len(all_edges) > MAX_GRID_EDGES:
        truncated = True

    selected_names = set(sorted(names)[:MAX_GRID_NODES])
    bounded_edges = tuple(
        edge
        for edge in all_edges[:MAX_GRID_EDGES]
        if edge.source in selected_names and edge.target in selected_names
    )
    observed_ports: dict[str, list[str]] = {}
    for port in sorted(graph_ports):
        observed_ports.setdefault(_node_id(port), []).append(port)

    configured_nodes = {node for edge in configured for node in edge}
    evidence_by_node: dict[str, Evidence] = {}
    for name in names:
        if name in live_nodes:
            evidence = Evidence.LIVE
        elif name in capture_node_ids:
            evidence = Evidence.CAPTURE
        elif name in configured_nodes:
            evidence = Evidence.CONFIGURED
        elif name in sink_names or name in source_names or name in playback_names:
            evidence = Evidence.OBSERVED
        else:
            evidence = Evidence.UNKNOWN
        evidence_by_node[name] = evidence

    if graph_status == "verified" and graph is not None and graph.status == "verified":
        for edge in configured_edges:
            if edge.evidence is Evidence.UNAVAILABLE:
                absent = (
                    edge.source
                    if edge.source not in live_nodes | sink_names | source_names
                    else edge.target
                )
                evidence_by_node[absent] = Evidence.UNAVAILABLE

    nodes: list[GridNode] = []
    for name in sorted(selected_names):
        group, label, inferred_detail = _classify(
            name,
            playback_names=playback_names,
            sink_names=sink_names,
            source_names=source_names,
        )
        detail_parts = [inferred_detail] if inferred_detail else []
        ports = tuple(observed_ports.get(name, ()))
        if ports:
            detail_parts.append(f"{len(ports)} observed PipeWire port(s)")
        if name in sink_inventory:
            sink = sink_inventory[name]
            detail_parts.append(f"Sink state: {getattr(sink, 'state', 'unknown')}")
        if name in source_inventory:
            detail_parts.append(f"Source state: {source_inventory[name]}")
        nodes.append(
            GridNode(
                id=name,
                label=label,
                group=group,
                evidence=evidence_by_node.get(name, Evidence.UNKNOWN),
                detail="; ".join(detail_parts),
                ports=ports,
            )
        )

    model_status = graph_status
    if truncated:
        model_status = "truncated"
        graph_detail = (
            f"Grid limited to {MAX_GRID_NODES} nodes and {MAX_GRID_EDGES} edges; "
            "inventory is incomplete"
        )
    return GridModel(
        nodes=tuple(nodes),
        edges=bounded_edges,
        status=model_status,
        detail=graph_detail,
        taken_at=graph_taken_at,
        truncated=truncated,
        consumer_status=capture_status,
        consumer_detail=capture_detail,
        consumer_count=len(capture_clients.clients) if capture_clients else 0,
        microphone_intent_status=(route_intent.microphone_status if route_intent else "unknown"),
        vesktop_status=route_intent.vesktop_status if route_intent else "unknown",
        vesktop_targets=route_intent.vesktop_targets if route_intent else (),
        route_health=notes,
    )
