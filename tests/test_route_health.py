from __future__ import annotations

from hearth.audio.route_health import (
    RecoveryAction,
    RouteState,
    execute_link_repair,
    inspect_bus,
    parse_link_inventory,
    plan_route_recovery,
    recovery_is_safe,
    verify_bus,
)

BUS = "vm_game"
SINK = "astro.chat"
NODE = "123-45"


def _graph(*, missing: set[str] | None = None, node: str = NODE) -> str:
    missing = missing or set()
    blocks = [
        (f"{BUS}:monitor_FL", f"  |-> input.loopback-{node}:input_FL", "input_FL"),
        (f"{BUS}:monitor_FR", f"  |-> input.loopback-{node}:input_FR", "input_FR"),
        (f"{SINK}:playback_FL", f"  |<- output.loopback-{node}:output_FL", "output_FL"),
        (f"{SINK}:playback_FR", f"  |<- output.loopback-{node}:output_FR", "output_FR"),
        (f"input.loopback-{node}:input_FL", f"  |<- {BUS}:monitor_FL", "input_FL"),
        (f"input.loopback-{node}:input_FR", f"  |<- {BUS}:monitor_FR", "input_FR"),
        (f"output.loopback-{node}:output_FL", f"  |-> {SINK}:playback_FL", "output_FL"),
        (f"output.loopback-{node}:output_FR", f"  |-> {SINK}:playback_FR", "output_FR"),
    ]
    rows: list[str] = []
    for port, link, edge_name in blocks:
        rows.append(port)
        if edge_name not in missing:
            rows.append(link)
    return "\n".join(rows) + "\n"


def _ports(node: str = NODE) -> set[str]:
    return {
        f"{BUS}:monitor_FL",
        f"{BUS}:monitor_FR",
        f"{SINK}:playback_FL",
        f"{SINK}:playback_FR",
        f"input.loopback-{node}:input_FL",
        f"input.loopback-{node}:input_FR",
        f"output.loopback-{node}:output_FL",
        f"output.loopback-{node}:output_FR",
    }


def test_parser_reads_port_inventory_and_both_link_directions() -> None:
    inventory = parse_link_inventory(_graph())
    assert f"{BUS}:monitor_FL" in inventory.ports
    assert f"{SINK}:playback_FR" in inventory.ports
    assert (
        f"{BUS}:monitor_FL",
        f"input.loopback-{NODE}:input_FL",
    ) in inventory.edges
    assert (
        f"output.loopback-{NODE}:output_FR",
        f"{SINK}:playback_FR",
    ) in inventory.edges


def test_verifies_four_stereo_edges_through_exactly_one_loopback() -> None:
    health = verify_bus(BUS, SINK, set(parse_link_inventory(_graph()).edges))
    assert health.state is RouteState.HEALTHY
    assert health.verified_links == 4


def test_missing_one_edge_is_degraded_with_accurate_count() -> None:
    listing = _graph(missing={"input_FR"})
    health = verify_bus(BUS, SINK, set(parse_link_inventory(listing).edges))
    assert health.state is RouteState.DEGRADED
    assert health.verified_links == 3


def test_split_stereo_nodes_are_not_healthy() -> None:
    listing = _graph().replace(f"input.loopback-{NODE}:input_FR", "input.loopback-999-88:input_FR")
    health = verify_bus(BUS, SINK, set(parse_link_inventory(listing).edges))
    assert health.state is RouteState.DEGRADED


def test_duplicate_complete_paths_are_degraded_not_silently_accepted() -> None:
    listing = _graph() + _graph(node="999-88")
    health = verify_bus(BUS, SINK, set(parse_link_inventory(listing).edges))
    assert health.state is RouteState.DEGRADED


def test_wrong_sink_does_not_count_as_healthy() -> None:
    listing = _graph().replace(SINK, "astro.game")
    health = verify_bus(BUS, SINK, set(parse_link_inventory(listing).edges))
    assert health.state is RouteState.DEGRADED


def test_failed_inventory_is_unknown_not_degraded() -> None:
    health = inspect_bus(BUS, SINK, None, sink_present=None)
    assert health.state is RouteState.UNKNOWN
    assert not recovery_is_safe(health.state)


def test_known_missing_sink_is_unavailable() -> None:
    health = inspect_bus(BUS, SINK, "", sink_present=False)
    assert health.state is RouteState.UNAVAILABLE
    assert not recovery_is_safe(health.state)


def test_only_positive_degraded_state_is_repair_eligible() -> None:
    assert recovery_is_safe(RouteState.DEGRADED)
    assert not recovery_is_safe(RouteState.HEALTHY)
    assert not recovery_is_safe(RouteState.UNKNOWN)
    assert not recovery_is_safe(RouteState.UNAVAILABLE)


def test_missing_route_without_module_plans_one_exact_loopback_creation() -> None:
    inventory = parse_link_inventory(
        f"{BUS}:monitor_FL\n{BUS}:monitor_FR\n{SINK}:playback_FL\n{SINK}:playback_FR\n"
    )
    plan = plan_route_recovery(BUS, SINK, inventory, sink_present=True, module_exists=False)
    assert plan.action is RecoveryAction.CREATE_LOOPBACK
    assert plan.links == ()


def test_existing_module_without_graph_anchor_is_unknown_not_recreated() -> None:
    inventory = parse_link_inventory(
        f"{BUS}:monitor_FL\n{BUS}:monitor_FR\n"
        f"{SINK}:playback_FL\n{SINK}:playback_FR\n"
        f"input.loopback-{NODE}:input_FL\ninput.loopback-{NODE}:input_FR\n"
        f"output.loopback-{NODE}:output_FL\noutput.loopback-{NODE}:output_FR\n"
    )
    plan = plan_route_recovery(BUS, SINK, inventory, sink_present=True, module_exists=True)
    assert plan.health.state is RouteState.UNKNOWN
    assert plan.action is RecoveryAction.NONE


def test_unmatched_half_route_is_not_repaired_or_duplicated() -> None:
    listing = (
        f"{BUS}:monitor_FL\n  |-> input.loopback-{NODE}:input_FL\n"
        f"{BUS}:monitor_FR\n{SINK}:playback_FL\n{SINK}:playback_FR\n"
    )
    inventory = parse_link_inventory(listing)
    plan = plan_route_recovery(BUS, SINK, inventory, sink_present=True, module_exists=False)
    assert plan.health.state is RouteState.UNKNOWN
    assert plan.action is RecoveryAction.NONE


def test_single_anchored_loopback_plans_only_the_missing_edge() -> None:
    listing = _graph(missing={"output_FR"})
    inventory = parse_link_inventory(listing)
    plan = plan_route_recovery(BUS, SINK, inventory, sink_present=True, module_exists=True)
    assert plan.health.state is RouteState.DEGRADED
    assert plan.action is RecoveryAction.CONNECT_LINKS
    assert plan.links == ((f"output.loopback-{NODE}:output_FR", f"{SINK}:playback_FR"),)


def test_multiple_possible_loopback_nodes_are_unknown() -> None:
    listing = _graph() + (
        f"{BUS}:monitor_FL\n  |-> input.loopback-999-88:input_FL\n"
        f"{SINK}:playback_FL\n  |<- output.loopback-999-88:output_FL\n"
    )
    plan = plan_route_recovery(
        BUS,
        SINK,
        parse_link_inventory(listing),
        sink_present=True,
        module_exists=True,
    )
    assert plan.health.state is RouteState.UNKNOWN
    assert plan.action is RecoveryAction.NONE


def test_recovery_waits_for_all_ports_of_identified_loopback() -> None:
    inventory = parse_link_inventory(_graph().replace("output.loopback-123-45:output_FR\n", ""))
    plan = plan_route_recovery(BUS, SINK, inventory, sink_present=True, module_exists=True)
    assert plan.health.state is RouteState.UNKNOWN
    assert plan.action is RecoveryAction.NONE


def test_recovery_never_runs_for_unknown_or_unavailable_inventory() -> None:
    for plan in (
        plan_route_recovery(BUS, SINK, None, sink_present=True, module_exists=True),
        plan_route_recovery(
            BUS,
            SINK,
            parse_link_inventory(""),
            sink_present=None,
            module_exists=True,
        ),
        plan_route_recovery(
            BUS,
            SINK,
            parse_link_inventory(""),
            sink_present=False,
            module_exists=True,
        ),
    ):
        calls: list[tuple[str, ...]] = []
        assert (
            execute_link_repair(
                plan,
                lambda argv, calls=calls: calls.append(tuple(argv)),
            )
            == 0
        )
        assert calls == []


def test_executor_runs_only_exact_planned_pw_link_commands() -> None:
    plan = plan_route_recovery(
        BUS,
        SINK,
        parse_link_inventory(_graph(missing={"input_FR"})),
        sink_present=True,
        module_exists=True,
    )
    calls: list[tuple[str, ...]] = []
    count = execute_link_repair(plan, lambda argv: calls.append(tuple(argv)))
    assert count == 1
    assert calls == [("pw-link", f"{BUS}:monitor_FR", f"input.loopback-{NODE}:input_FR")]


def test_successful_repair_plan_becomes_noop_when_graph_is_verified() -> None:
    plan = plan_route_recovery(
        BUS,
        SINK,
        parse_link_inventory(_graph(missing={"input_FR"})),
        sink_present=True,
        module_exists=True,
    )
    assert plan.action is RecoveryAction.CONNECT_LINKS
    # The fresh graph after the planned connection now contains all four links.
    verified = plan_route_recovery(
        BUS,
        SINK,
        parse_link_inventory(_graph()),
        sink_present=True,
        module_exists=True,
    )
    assert verified.health.state is RouteState.HEALTHY
    assert verified.action is RecoveryAction.NONE
