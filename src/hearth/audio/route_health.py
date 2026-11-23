"""Read-only PipeWire route inspection and conservative recovery planning.

The parser consumes ``pw-link -iol`` output. Planning is pure and never
changes the PipeWire graph; callers must explicitly execute its bounded
``pw-link`` actions and verify the graph again.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum


class RouteState(StrEnum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNKNOWN = "unknown"
    UNAVAILABLE = "unavailable"


class RecoveryAction(StrEnum):
    NONE = "none"
    CREATE_LOOPBACK = "create_loopback"
    CONNECT_LINKS = "connect_links"


@dataclass(frozen=True, slots=True)
class BusRouteHealth:
    bus: str
    state: RouteState
    verified_links: int
    expected_links: int = 4
    detail: str = ""


@dataclass(frozen=True, slots=True)
class LinkInventory:
    ports: frozenset[str]
    edges: frozenset[tuple[str, str]]


@dataclass(frozen=True, slots=True)
class RecoveryPlan:
    health: BusRouteHealth
    action: RecoveryAction = RecoveryAction.NONE
    links: tuple[tuple[str, str], ...] = ()
    detail: str = ""


_EDGE = re.compile(r"^\s*\|->\s+(.+?)\s*$|^\s*\|<-\s+(.+?)\s*$")
_LOOPBACK_INPUT = re.compile(r"^input\.loopback-(?P<node>[^:]+):input_(?P<channel>FL|FR)$")
_LOOPBACK_OUTPUT = re.compile(r"^output\.loopback-(?P<node>[^:]+):output_(?P<channel>FL|FR)$")


def parse_link_inventory(listing: str) -> LinkInventory:
    """Parse ``pw-link -iol`` into known ports and directed output->input edges."""
    ports: set[str] = set()
    edges: set[tuple[str, str]] = set()
    current = ""
    for line in listing.splitlines():
        if not line[:1].isspace() and ":" in line:
            current = line.strip()
            ports.add(current)
            continue
        match = _EDGE.match(line)
        if not match or not current:
            continue
        if match.group(1) is not None:
            edges.add((current, match.group(1)))
        else:
            edges.add((match.group(2), current))
    return LinkInventory(frozenset(ports), frozenset(edges))


def parse_link_edges(listing: str) -> set[tuple[str, str]]:
    """Compatibility helper for callers that only need directed link edges."""
    return set(parse_link_inventory(listing).edges)


def _bus_edges(
    bus: str,
    sink: str,
    edges: set[tuple[str, str]] | frozenset[tuple[str, str]],
) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    inputs: dict[str, set[str]] = {"FL": set(), "FR": set()}
    outputs: dict[str, set[str]] = {"FL": set(), "FR": set()}
    for output, target in edges:
        for channel in ("FL", "FR"):
            if output == f"{bus}:monitor_{channel}":
                match = _LOOPBACK_INPUT.fullmatch(target)
                if match and match.group("channel") == channel:
                    inputs[channel].add(match.group("node"))

    # Astro Chat is shared by all VM buses. Its playback ports therefore have
    # links from every bus's loopback; only count outputs whose loopback is
    # independently anchored to this bus's own monitor source.
    source_nodes = set().union(*inputs.values())
    for output, target in edges:
        for channel in ("FL", "FR"):
            if target == f"{sink}:playback_{channel}":
                match = _LOOPBACK_OUTPUT.fullmatch(output)
                if (
                    match
                    and match.group("channel") == channel
                    and match.group("node") in source_nodes
                ):
                    outputs[channel].add(match.group("node"))
    return inputs, outputs


def verify_bus(
    bus: str,
    sink: str,
    edges: set[tuple[str, str]],
    *,
    sink_present: bool = True,
) -> BusRouteHealth:
    """Verify four exact stereo edges through one and only one loopback node."""
    if not sink_present:
        return BusRouteHealth(
            bus,
            RouteState.UNAVAILABLE,
            0,
            detail="configured sink is absent",
        )

    inputs, outputs = _bus_edges(bus, sink, edges)
    verified = sum(bool(inputs[channel]) + bool(outputs[channel]) for channel in ("FL", "FR"))
    left_complete = inputs["FL"] & outputs["FL"]
    right_complete = inputs["FR"] & outputs["FR"]
    complete_nodes = left_complete & right_complete
    related_nodes = set().union(*inputs.values(), *outputs.values())

    if len(complete_nodes) == 1 and related_nodes == complete_nodes:
        return BusRouteHealth(bus, RouteState.HEALTHY, 4, detail="four stereo links verified")
    return BusRouteHealth(
        bus,
        RouteState.DEGRADED,
        verified,
        detail="stereo path is incomplete, split, or duplicated",
    )


def inspect_bus(
    bus: str,
    sink: str,
    link_listing: str | None,
    *,
    sink_present: bool | None,
) -> BusRouteHealth:
    """Classify a route without turning failed inventory into a false alarm.

    ``None`` means a query failed. An explicit false sink presence is
    UNAVAILABLE; failed sink or PipeWire inventory is UNKNOWN.
    """
    if sink_present is None or link_listing is None:
        return BusRouteHealth(
            bus,
            RouteState.UNKNOWN,
            0,
            detail="PipeWire or sink inventory query failed",
        )
    if not sink_present:
        return BusRouteHealth(
            bus,
            RouteState.UNAVAILABLE,
            0,
            detail="configured sink is absent",
        )
    return verify_bus(bus, sink, set(parse_link_inventory(link_listing).edges))


def plan_route_recovery(
    bus: str,
    sink: str,
    inventory: LinkInventory | None,
    *,
    sink_present: bool | None,
    module_exists: bool | None,
) -> RecoveryPlan:
    """Build a fail-closed, idempotent recovery plan for one VM bus.

    A module can be created only when the module query succeeded and no exact
    source->sink loopback exists. Existing links are repaired only when one
    loopback node is unambiguously anchored on both the source and sink sides.
    """
    if inventory is None or sink_present is None or module_exists is None:
        health = BusRouteHealth(
            bus,
            RouteState.UNKNOWN,
            0,
            detail="required inventory query failed",
        )
        return RecoveryPlan(health, detail="no mutation permitted while inventory is unknown")
    if not sink_present:
        health = BusRouteHealth(bus, RouteState.UNAVAILABLE, 0, detail="configured sink is absent")
        return RecoveryPlan(health, detail="wait for the configured Chat endpoint")

    health = verify_bus(bus, sink, set(inventory.edges))
    if health.state is RouteState.HEALTHY:
        return RecoveryPlan(health)

    required_endpoints = {
        f"{bus}:monitor_FL",
        f"{bus}:monitor_FR",
        f"{sink}:playback_FL",
        f"{sink}:playback_FR",
    }
    missing_endpoints = required_endpoints - inventory.ports
    if missing_endpoints:
        state = (
            RouteState.UNAVAILABLE
            if any(port.startswith(f"{sink}:") for port in missing_endpoints)
            else RouteState.UNKNOWN
        )
        detail = "missing endpoint ports: " + ", ".join(sorted(missing_endpoints))
        unavailable = BusRouteHealth(bus, state, health.verified_links, detail=detail)
        return RecoveryPlan(unavailable, detail="wait for the VM bus and Astro Chat ports")

    inputs, outputs = _bus_edges(bus, sink, inventory.edges)
    source_nodes = set().union(*inputs.values())
    sink_nodes = set().union(*outputs.values())
    candidates = source_nodes & sink_nodes

    if len(candidates) == 0:
        if module_exists:
            unknown = BusRouteHealth(
                bus,
                RouteState.UNKNOWN,
                health.verified_links,
                detail="existing loopback has no graph anchor; refusing to guess its node",
            )
            return RecoveryPlan(unknown, detail="no safe PipeWire node identity")
        if source_nodes or sink_nodes:
            unknown = BusRouteHealth(
                bus,
                RouteState.UNKNOWN,
                health.verified_links,
                detail="unmatched loopback edges exist; refusing to create a duplicate",
            )
            return RecoveryPlan(unknown, detail="existing graph cannot be attributed safely")
        return RecoveryPlan(
            health,
            RecoveryAction.CREATE_LOOPBACK,
            detail=f"create exact {bus}.monitor -> {sink} loopback",
        )

    if len(candidates) != 1:
        unknown = BusRouteHealth(
            bus,
            RouteState.UNKNOWN,
            health.verified_links,
            detail="multiple loopback nodes are plausible owners of this route",
        )
        return RecoveryPlan(unknown, detail="ambiguous PipeWire node identity")

    node = next(iter(candidates))
    # Do not repair when the source or destination is already associated with
    # additional loopback nodes. Those may be independent paths, and removing
    # or merging them would require ownership information we do not possess.
    if source_nodes != {node} or sink_nodes != {node}:
        unknown = BusRouteHealth(
            bus,
            RouteState.UNKNOWN,
            health.verified_links,
            detail="additional loopback nodes make route ownership ambiguous",
        )
        return RecoveryPlan(unknown, detail="no destructive or additive repair permitted")

    input_ports = {f"input.loopback-{node}:input_{channel}" for channel in ("FL", "FR")}
    output_ports = {f"output.loopback-{node}:output_{channel}" for channel in ("FL", "FR")}
    missing_loopback_ports = (input_ports | output_ports) - inventory.ports
    if missing_loopback_ports:
        unknown = BusRouteHealth(
            bus,
            RouteState.UNKNOWN,
            health.verified_links,
            detail="identified loopback ports are not all present",
        )
        return RecoveryPlan(unknown, detail="wait for PipeWire loopback ports")

    expected = {
        (f"{bus}:monitor_{channel}", f"input.loopback-{node}:input_{channel}")
        for channel in ("FL", "FR")
    } | {
        (f"output.loopback-{node}:output_{channel}", f"{sink}:playback_{channel}")
        for channel in ("FL", "FR")
    }
    missing_links = tuple(sorted(expected - set(inventory.edges)))
    if not missing_links:
        return RecoveryPlan(
            health,
            detail="graph is present but includes additional duplicate nodes",
        )
    return RecoveryPlan(
        health,
        RecoveryAction.CONNECT_LINKS,
        missing_links,
        detail=f"reconnect {len(missing_links)} missing edge(s) on loopback-{node}",
    )


def execute_link_repair(
    plan: RecoveryPlan,
    runner: Callable[[Sequence[str]], object],
) -> int:
    """Execute only a prevalidated CONNECT_LINKS plan; no unload/volume changes."""
    if plan.action is not RecoveryAction.CONNECT_LINKS:
        return 0
    repaired = 0
    for output, input_port in plan.links:
        runner(("pw-link", output, input_port))
        repaired += 1
    return repaired


def recovery_is_safe(state: RouteState) -> bool:
    """Only a positively observed incomplete graph may be considered for repair."""
    return state is RouteState.DEGRADED
