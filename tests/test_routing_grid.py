"""Pure Routing Grid model tests. All inventories are synthetic."""

from __future__ import annotations

from hearth.audio.route_health import BusRouteHealth, RouteState
from hearth.collector import (
    CaptureClient,
    CaptureClientSnapshot,
    GraphSnapshot,
    RouteIntentSnapshot,
    SinkState,
)
from hearth.routing_grid import (
    MAX_GRID_EDGES,
    MAX_GRID_NODES,
    Evidence,
    Group,
    build_model,
)


def graph(
    *,
    ports: tuple[str, ...] | list[str] = (),
    edges: tuple[tuple[str, str], ...] | list[tuple[str, str]] = (),
    status: str = "verified",
    detail: str = "synthetic graph",
    taken_at: float = 100.0,
) -> GraphSnapshot:
    return GraphSnapshot(tuple(ports), tuple(edges), status, detail, taken_at)


def intent(
    *,
    microphone_edges: tuple[tuple[str, str], ...] = (),
    microphone_status: str = "verified",
    vesktop_targets: tuple[str, ...] = (),
    vesktop_status: str = "verified",
) -> RouteIntentSnapshot:
    return RouteIntentSnapshot(
        microphone_edges=microphone_edges,
        microphone_status=microphone_status,
        vesktop_targets=vesktop_targets,
        vesktop_status=vesktop_status,
        taken_at=100.0,
    )


def test_live_edges_are_node_normalized_and_duplicate_free() -> None:
    source = "vm_game:monitor_FL"
    target = "input.loopback-421-13:input_FL"
    model = build_model(graph(ports=(source, target), edges=((source, target), (source, target))))

    assert model.status == "verified"
    assert len(model.edges) == 1
    edge = model.edges[0]
    assert (edge.source, edge.target) == ("vm_game", "input.loopback-421-13")
    assert (edge.source_port, edge.target_port) == (source, target)
    assert edge.evidence is Evidence.LIVE
    assert {node.evidence for node in model.nodes} == {Evidence.LIVE}


def test_configured_edge_is_not_presented_as_live() -> None:
    model = build_model(
        graph(ports=("sm7b_mono:output", "mic_b1:input_FL")),
        route_intent=intent(microphone_edges=(("sm7b_mono", "mic_b1"),)),
    )
    assert len(model.edges) == 1
    assert model.edges[0].evidence is Evidence.CONFIGURED
    assert "not verified as a live edge" in model.edges[0].detail
    assert model.microphone_intent_status == "verified"


def test_live_edge_wins_over_matching_configured_intent() -> None:
    edge = ("sm7b_mono:output", "mic_b1:input_FL")
    model = build_model(
        graph(ports=edge, edges=(edge,)),
        route_intent=intent(microphone_edges=(("sm7b_mono", "mic_b1"),)),
    )
    assert len(model.edges) == 1
    assert model.edges[0].evidence is Evidence.LIVE


def test_live_and_configured_mismatch_are_shown_as_separate_evidence() -> None:
    live = ("astro_mic_48k:capture_FL", "mic_b1:input_FL")
    ports = ("sm7b_mono:output", *live)
    model = build_model(
        graph(ports=ports, edges=(live,)),
        route_intent=intent(microphone_edges=(("sm7b_mono", "mic_b1"),)),
    )
    assert {(edge.source, edge.target, edge.evidence) for edge in model.edges} == {
        ("astro_mic_48k", "mic_b1", Evidence.LIVE),
        ("sm7b_mono", "mic_b1", Evidence.CONFIGURED),
    }


def test_configured_endpoint_absent_from_complete_graph_is_unavailable() -> None:
    model = build_model(
        graph(ports=("mic_b1:playback_FL",)),
        route_intent=intent(microphone_edges=(("sm7b_mono", "mic_b1"),)),
    )
    assert model.edges[0].evidence is Evidence.UNAVAILABLE


def test_missing_or_failed_graph_does_not_claim_no_live_links() -> None:
    missing = build_model(None)
    failed = build_model(graph(status="unknown", detail="probe failed"))
    configured = build_model(
        graph(status="unknown", detail="probe failed"),
        route_intent=intent(microphone_edges=(("sm7b_mono", "mic_b1"),)),
    )
    assert missing.status == failed.status == "unknown"
    assert failed.detail == "probe failed"
    assert missing.edges == failed.edges == ()
    assert configured.edges[0].evidence is Evidence.CONFIGURED


def test_verified_empty_graph_is_not_unknown() -> None:
    model = build_model(graph())
    assert model.status == "verified"
    assert model.nodes == ()
    assert model.edges == ()


def test_graph_stale_after_five_seconds_and_marks_edges_stale() -> None:
    edge = ("vm_game:monitor_FL", "vm_share:playback_FL")
    graph_snapshot = graph(ports=edge, edges=(edge,), taken_at=100.0)
    fresh = build_model(graph_snapshot, now=105.0)
    stale = build_model(graph_snapshot, now=105.01)
    assert fresh.status == "verified"
    assert fresh.edges[0].evidence is Evidence.LIVE
    assert stale.status == "stale"
    assert stale.edges[0].evidence is Evidence.STALE
    assert len(stale.nodes) == 2


def test_grid_caps_nodes_and_edges_and_marks_incomplete() -> None:
    edges = tuple((f"source{i}:out", f"target{i}:in") for i in range(MAX_GRID_EDGES + 20))
    ports = tuple(port for edge in edges for port in edge)
    model = build_model(graph(ports=ports, edges=edges))
    assert model.truncated
    assert model.status == "truncated"
    assert len(model.nodes) <= MAX_GRID_NODES
    assert len(model.edges) <= MAX_GRID_EDGES
    assert all(edge.source in {node.id for node in model.nodes} for edge in model.edges)
    assert all(edge.target in {node.id for node in model.nodes} for edge in model.edges)


def test_configured_intent_is_also_bounded() -> None:
    configured = tuple((f"source{i}", f"target{i}") for i in range(MAX_GRID_EDGES + 10))
    model = build_model(graph(), route_intent=intent(microphone_edges=configured))
    assert model.truncated
    assert len(model.nodes) <= MAX_GRID_NODES
    assert len(model.edges) <= MAX_GRID_EDGES
    assert all(edge.evidence in {Evidence.CONFIGURED, Evidence.UNAVAILABLE} for edge in model.edges)


def test_node_groups_use_known_roles_and_unknown_nodes_are_conservative() -> None:
    edges = (
        ("sm7b_mono:output", "Carla:audio-in1"),
        ("Carla:audio-out1", "mic_b1:playback_FL"),
        ("vm_game:monitor_FL", "vm_share:playback_FL"),
        ("random-device:output", "random-client:input"),
    )
    ports = tuple(port for edge in edges for port in edge)
    model = build_model(graph(ports=ports, edges=edges))
    groups = {node.id: node.group for node in model.nodes}
    assert groups["sm7b_mono"] is Group.INPUTS
    assert groups["Carla"] is Group.PROCESSING
    assert groups["mic_b1"] is Group.BUSES
    assert groups["vm_game"] is Group.BUSES
    assert groups["vm_share"] is Group.BUSES
    assert groups["random-device"] is Group.PROCESSING
    assert groups["random-client"] is Group.PROCESSING


def test_source_output_inventory_creates_capture_edge_but_not_app_name_claim() -> None:
    capture = CaptureClientSnapshot(
        clients=(CaptureClient(index=8, client=22, source_index=4, source="mic_b1"),),
        status="verified",
        detail="One source-output observed",
        taken_at=100.0,
    )
    model = build_model(graph(), capture_clients=capture)
    assert model.consumer_status == "verified"
    assert len(model.edges) == 1
    assert model.edges[0].evidence is Evidence.CAPTURE
    assert model.edges[0].source == "mic_b1"
    assert model.edges[0].target == "capture-client-22"
    assert "application name was not queried" in next(
        node.detail for node in model.nodes if node.id == "capture-client-22"
    )


def test_capture_query_failure_is_unknown_not_no_consumers() -> None:
    unknown = CaptureClientSnapshot(status="unknown", detail="source-outputs timed out")
    model = build_model(graph(), capture_clients=unknown)
    assert model.consumer_status == "unknown"
    assert "timed out" in model.consumer_detail
    assert model.edges == ()


def test_successful_empty_capture_inventory_is_explicitly_empty() -> None:
    empty = CaptureClientSnapshot(status="verified", detail="Enumerated no clients")
    model = build_model(graph(), capture_clients=empty)
    assert model.consumer_status == "verified"
    assert model.edges == ()


def test_sink_input_playback_does_not_fabricate_microphone_consumer_edges() -> None:
    class Playback:
        name = "Vesktop"

    model = build_model(graph(), sink_inputs={"vm_game": [Playback()]})
    assert any(node.id == "Vesktop" for node in model.nodes)
    assert model.consumer_status == "unknown"
    assert not any(edge.evidence is Evidence.CAPTURE for edge in model.edges)


def test_vesktop_direct_selection_is_separate_from_screen_share_bus() -> None:
    live = ("vm_game:monitor_FL", "vm_share:playback_FL")
    model = build_model(
        graph(ports=live, edges=(live,)),
        route_intent=intent(vesktop_targets=("game", "music")),
    )
    assert model.vesktop_status == "verified"
    assert model.vesktop_targets == ("game", "music")
    assert any(node.id == "vm_share" for node in model.nodes)
    # Vesktop is saved metadata only. No direct-selection edge is invented.
    assert all(edge.target != "Vesktop" for edge in model.edges)
    assert all(edge.source != "Vesktop" for edge in model.edges)


def test_partial_four_link_route_marks_its_loopback_edges_degraded() -> None:
    edge = ("vm_game:monitor_FL", "input.loopback-421-13:input_FL")
    health = {
        "vm_game.monitor": {
            "alsa_output.test": BusRouteHealth(
                bus="vm_game",
                state=RouteState.DEGRADED,
                verified_links=3,
                expected_links=4,
                detail="right channel incomplete",
            )
        }
    }
    model = build_model(graph(ports=edge, edges=(edge,)), route_health=health)
    assert model.edges[0].evidence is Evidence.DEGRADED
    assert "partially verified" in model.edges[0].detail


def test_route_health_degradation_is_preserved_separately() -> None:
    health = {
        "vm_game.monitor": {
            "alsa_output.test": BusRouteHealth(
                bus="vm_game",
                state=RouteState.DEGRADED,
                verified_links=3,
                expected_links=4,
                detail="right channel incomplete",
            )
        }
    }
    model = build_model(graph(), route_health=health)
    assert len(model.route_health) == 1
    assert model.route_health[0].state == "degraded"
    assert (model.route_health[0].verified_links, model.route_health[0].expected_links) == (3, 4)


def test_unchanged_model_fingerprint_ignores_snapshot_time() -> None:
    first = build_model(graph(taken_at=100.0))
    second = build_model(graph(taken_at=100.5))
    assert first.fingerprint == second.fingerprint


def test_stale_model_fingerprint_is_stable_across_collector_ticks() -> None:
    snapshot = graph(taken_at=100.0)
    first = build_model(snapshot, now=106.0)
    second = build_model(snapshot, now=106.5)
    assert first.status == "stale"
    assert second.status == "stale"
    assert first.fingerprint == second.fingerprint


def test_sink_and_source_observations_enrich_nodes_without_creating_edges() -> None:
    sinks = {"vm_game": SinkState(name="vm_game", state="RUNNING", muted=False, volume=55)}
    sources = {"sm7b_mono": "IDLE"}
    model = build_model(graph(), sinks=sinks, sources=sources)
    assert model.nodes[0].group in set(Group)
    assert any("Sink state: RUNNING" in node.detail for node in model.nodes)
    assert any("Source state: IDLE" in node.detail for node in model.nodes)
    assert model.edges == ()
