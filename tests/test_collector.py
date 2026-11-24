"""Collector tests, run against output recorded from the live rig."""

from __future__ import annotations

import os
import threading
from pathlib import Path

import pytest

from hearth import collector, mic_routes, share
from hearth.audio.route_health import RouteState
from hearth.errors import CommandError


@pytest.fixture
def sinks(fixture_text):
    return collector.parse_sinks(fixture_text("pactl_sinks.txt"))


@pytest.fixture
def streams(fixture_text, sinks):
    return collector.parse_streams(
        fixture_text("pactl_sink_inputs.txt"), collector.index_map(sinks)
    )


def test_parse_sinks_finds_the_virtual_busses(sinks):
    for name in ("vm_game", "vm_chat", "vm_music", "laptop_audio", "mic_b1", "mic_b2"):
        assert name in sinks, f"{name} missing from parsed sinks"


def test_parse_sinks_reads_volume_mute_and_state(sinks):
    game = sinks["vm_game"]
    assert 0 <= game.volume <= 200
    assert isinstance(game.muted, bool)
    assert game.state in ("RUNNING", "IDLE", "SUSPENDED")
    assert game.index.isdigit()


def test_sink_volume_is_not_overwritten_by_base_volume(fixture_text):
    """Only the first Volume: line is the sink volume."""
    text = (
        "Sink #7\n"
        "\tState: RUNNING\n"
        "\tName: vm_game\n"
        "\tMute: no\n"
        "\tVolume: front-left: 36044 /  55% / -15.56 dB\n"
        "\tBase Volume: 65536 / 100% / 0.00 dB\n"
        "\tMonitor Source: vm_game.monitor\n"
    )
    assert collector.parse_sinks(text)["vm_game"].volume == 55


def test_index_map_round_trips(sinks):
    mapping = collector.index_map(sinks)
    assert mapping[sinks["vm_game"].index] == "vm_game"


def test_parse_streams_groups_apps_by_sink_name(streams):
    names = {s.name for group in streams.values() for s in group}
    assert {"vesktop", "Firefox"} <= names
    for sink, group in streams.items():
        assert sink and all(s.sink == sink for s in group)


def test_parse_streams_keeps_volume_and_mute(streams):
    every = [s for group in streams.values() for s in group]
    assert every, "expected at least one stream in the fixture"
    assert all(0 <= s.volume <= 200 for s in every)
    assert all(isinstance(s.muted, bool) for s in every)


def test_parse_streams_drops_entries_with_an_unknown_sink():
    text = 'Sink Input #99\n\tSink: 4242\n\tMute: no\n\t\tapplication.name = "Ghost"\n'
    assert collector.parse_streams(text, {"39": "vm_game"}) == {}


def test_parse_source_states(fixture_text):
    states = collector.parse_source_states(fixture_text("pactl_short_sources.txt"))
    assert states
    assert all(state in ("RUNNING", "IDLE", "SUSPENDED") for state in states.values())


def test_parse_capture_clients_maps_source_indices_and_skips_bad_rows(fixture_text):
    clients = collector.parse_capture_clients(
        fixture_text("pactl_short_source_outputs.txt"),
        {243: "sm7b_mono", 252: "astro_mic_48k"},
    )
    assert [
        (client.index, client.client, client.source_index, client.source) for client in clients
    ] == [
        (8, 23, 243, "sm7b_mono"),
        (9, 24, 252, "astro_mic_48k"),
    ]
    assert collector.parse_capture_clients("bad row\n", {}) == ()


def test_parsers_tolerate_empty_output():
    assert collector.parse_sinks("") == {}
    assert collector.parse_streams("", {}) == {}
    assert collector.parse_source_states("") == {}


def test_snapshot_accessors_and_unit_health():
    snap = collector.Snapshot(
        sinks={"vm_game": collector.SinkState(name="vm_game", volume=55, muted=True)},
        units={"a.service": "active", "b.service": "failed"},
    )
    assert snap.volume("vm_game") == 55
    assert snap.muted("vm_game") is True
    assert snap.volume("nope", default=7) == 7
    assert snap.muted("nope") is False
    assert snap.all_units_active is False
    assert snap.failed_units == ["b.service"]


def test_all_units_active_is_false_with_no_units():
    assert collector.Snapshot().all_units_active is False


@pytest.mark.skipif(os.name == "nt", reason="Linux-only PipeWire collector")
def test_graph_inventory_is_bounded_and_preserves_status() -> None:
    listing = "\n".join([f"node{i}:port" for i in range(collector.GRAPH_MAX_PORTS + 20)])
    graph = collector.bounded_graph(listing, taken_at=123.0)
    assert graph.status == "truncated"
    assert len(graph.ports) == collector.GRAPH_MAX_PORTS
    assert graph.edges == ()
    assert graph.taken_at == 123.0
    assert "capped" in graph.detail


def test_empty_graph_is_verified_not_unknown() -> None:
    empty = collector._graph_from_listing("", taken_at=5.0)
    failed = collector.graph_unknown("query failed", taken_at=5.0)
    assert empty.status == "verified"
    assert empty.ports == () and empty.edges == ()
    assert failed.status == "unknown"
    assert failed.detail == "query failed"


def test_collect_keeps_core_audio_read_failures_distinct_from_empty_graph(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def failed_read(*args: str) -> str:
        raise CommandError(["pactl", *args], stderr="PipeWire is temporarily unavailable")

    monkeypatch.setattr(collector.pactl, "raw_checked", failed_read)

    with pytest.raises(CommandError, match="PipeWire is temporarily unavailable"):
        collector.collect([])


@pytest.mark.skipif(os.name == "nt", reason="Linux-only PipeWire collector")
def test_collect_builds_route_graph_off_the_ui_thread(
    monkeypatch: pytest.MonkeyPatch, fixture_text
) -> None:
    outputs = {
        ("list", "sinks"): fixture_text("pactl_sinks.txt"),
        ("list", "sink-inputs"): fixture_text("pactl_sink_inputs.txt"),
        ("list", "short", "sources"): fixture_text("pactl_short_sources.txt"),
    }
    monkeypatch.setattr(collector.pactl, "raw_checked", lambda *args: outputs[args])
    modules = collector.pactl.parse_modules(fixture_text("pactl_short_modules.txt"))
    module_options: dict[str, object] = {}

    def read_modules(**kwargs):
        module_options.update(kwargs)
        return modules

    monkeypatch.setattr(collector.pactl, "modules", read_modules)

    def read_optional(argv, **_kwargs):
        if argv[:2] == ["pw-link", "-iol"]:
            output = fixture_text("pw_link_vm_routes.txt")
        elif argv == ["pactl", "list", "short", "source-outputs"]:
            output = fixture_text("pactl_short_source_outputs.txt")
        else:
            raise AssertionError(f"unexpected collector command: {argv!r}")
        return type("Result", (), {"stdout": output})()

    monkeypatch.setattr(collector.proc, "run", read_optional)
    monkeypatch.setattr(
        collector,
        "_route_intent_snapshot",
        lambda: collector.RouteIntentSnapshot(
            microphone_edges=(("sm7b_mono", "mic_b1"),),
            microphone_status="verified",
            vesktop_targets=("game", "music"),
            vesktop_status="verified",
        ),
    )
    monkeypatch.setattr(collector.services, "states", lambda _units: {})
    monkeypatch.setattr(collector.pactl, "server_version", lambda: "1.4.11")
    monkeypatch.setattr(collector.pactl, "default_sink", lambda: "vm_game")

    snapshot = collector.collect([])

    assert "vm_game" in snapshot.sinks
    expected: dict[str, set[str]] = {}
    for loop in collector.pactl.select_loopbacks(modules):
        expected.setdefault(loop.source, set()).add(loop.sink)
    assert snapshot.routes == expected
    assert snapshot.route_health is not None
    assert snapshot.graph is not None
    assert snapshot.graph.status == "verified"
    assert snapshot.graph.taken_at > 0
    assert len(snapshot.graph.ports) > 0
    assert len(snapshot.graph.edges) > 0
    assert all(len(edge) == 2 for edge in snapshot.graph.edges)
    assert snapshot.graph.detail == "PipeWire graph inventory verified"
    assert snapshot.capture_clients is not None
    assert snapshot.capture_clients.status == "verified"
    assert [item.source for item in snapshot.capture_clients.clients] == [
        "sm7b_mono",
        "astro_mic_48k",
    ]
    assert snapshot.route_intent is not None
    assert snapshot.route_intent.microphone_edges == (("sm7b_mono", "mic_b1"),)
    assert snapshot.route_intent.vesktop_targets == ("game", "music")
    for source in ("vm_game.monitor", "vm_chat.monitor", "vm_music.monitor"):
        health = snapshot.route_health[source][
            "alsa_output.usb-Astro_Gaming_Astro_A50-00.stereo-chat"
        ]
        assert health.state is RouteState.HEALTHY
        assert health.verified_links == 4
    assert module_options == {"strict": True, "timeout": 0.75}
    # The parsed sink inventory already carries hardware mute state, avoiding
    # additional per-output mute commands from the GTK callback.
    assert isinstance(snapshot.sinks["vm_game"].muted, bool)


@pytest.mark.skipif(os.name == "nt", reason="Linux-only PipeWire collector")
def test_optional_route_query_failure_marks_routes_unknown_but_keeps_snapshot(
    monkeypatch: pytest.MonkeyPatch, fixture_text
) -> None:
    outputs = {
        ("list", "sinks"): fixture_text("pactl_sinks.txt"),
        ("list", "sink-inputs"): fixture_text("pactl_sink_inputs.txt"),
        ("list", "short", "sources"): fixture_text("pactl_short_sources.txt"),
    }
    monkeypatch.setattr(collector.pactl, "raw_checked", lambda *args: outputs[args])

    def failed_modules(**_kwargs):
        raise CommandError(["pactl", "list", "short", "modules"], stderr="module table timed out")

    monkeypatch.setattr(collector.pactl, "modules", failed_modules)
    monkeypatch.setattr(
        collector.proc,
        "run",
        lambda argv, **kwargs: type(
            "Result",
            (),
            {
                "stdout": (
                    fixture_text("pw_link_vm_routes.txt") if argv[:2] == ["pw-link", "-iol"] else ""
                )
            },
        )(),
    )
    monkeypatch.setattr(collector.services, "states", lambda _units: {})
    monkeypatch.setattr(collector.pactl, "server_version", lambda: "")
    monkeypatch.setattr(collector.pactl, "default_sink", lambda: "")

    snapshot = collector.collect([])

    assert snapshot.sinks
    assert snapshot.routes is None
    assert snapshot.route_health is None
    assert snapshot.graph is not None
    assert snapshot.graph.status == "verified"
    assert len(snapshot.graph.edges) > 0


@pytest.mark.skipif(os.name == "nt", reason="Linux-only PipeWire route collector")
def test_collect_marks_route_health_unknown_when_pw_link_query_fails(
    monkeypatch: pytest.MonkeyPatch, fixture_text
) -> None:
    outputs = {
        ("list", "sinks"): fixture_text("pactl_sinks.txt"),
        ("list", "sink-inputs"): fixture_text("pactl_sink_inputs.txt"),
        ("list", "short", "sources"): fixture_text("pactl_short_sources.txt"),
    }
    monkeypatch.setattr(collector.pactl, "raw_checked", lambda *args: outputs[args])
    modules = collector.pactl.parse_modules(fixture_text("pactl_short_modules.txt"))
    monkeypatch.setattr(collector.pactl, "modules", lambda **_kwargs: modules)

    def failed_links(*_args, **_kwargs):
        raise CommandError(["pw-link", "-iol"], stderr="PipeWire graph query timed out")

    monkeypatch.setattr(collector.proc, "run", failed_links)
    monkeypatch.setattr(collector.services, "states", lambda _units: {})
    monkeypatch.setattr(collector.pactl, "server_version", lambda: "")
    monkeypatch.setattr(collector.pactl, "default_sink", lambda: "")

    snapshot = collector.collect([])

    assert snapshot.routes is not None
    assert snapshot.route_health is None
    assert snapshot.graph is not None
    assert snapshot.graph.status == "unknown"
    assert "PipeWire graph query timed out" in snapshot.graph.detail
    assert snapshot.graph.edges == ()


def test_build_route_health_marks_partial_stereo_link_degraded(fixture_text):
    modules = collector.pactl.parse_modules(fixture_text("pactl_short_modules.txt"))
    graph = (
        fixture_text("pw_link_vm_routes.txt")
        .replace("  |-> input.loopback-421-13:input_FR\n", "")
        .replace("  |<- vm_game:monitor_FR\n", "")
    )
    health = collector.build_route_health(
        modules,
        graph,
        {
            "vm_game",
            "vm_chat",
            "vm_music",
            "alsa_output.usb-Astro_Gaming_Astro_A50-00.stereo-chat",
        },
    )
    game = health["vm_game.monitor"]["alsa_output.usb-Astro_Gaming_Astro_A50-00.stereo-chat"]
    # Missing a source-side edge retains the identified loopback but is not healthy.
    assert game.state is RouteState.DEGRADED
    assert game.verified_links == 3
    # The other two buses sharing Astro Chat do not affect this route's result.
    assert (
        health["vm_chat.monitor"]["alsa_output.usb-Astro_Gaming_Astro_A50-00.stereo-chat"].state
        is RouteState.HEALTHY
    )


def test_build_route_health_keeps_empty_pipewire_inventory_unknown(fixture_text):
    modules = collector.pactl.parse_modules(fixture_text("pactl_short_modules.txt"))
    health = collector.build_route_health(
        modules,
        "",
        {"vm_game", "vm_chat", "vm_music", "alsa_output.usb-Astro_Gaming_Astro_A50-00.stereo-chat"},
    )
    assert (
        health["vm_game.monitor"]["alsa_output.usb-Astro_Gaming_Astro_A50-00.stereo-chat"].state
        is RouteState.UNKNOWN
    )


def test_build_route_health_marks_duplicate_modules_unknown(fixture_text):
    modules = collector.pactl.parse_modules(fixture_text("pactl_short_modules.txt"))
    duplicate = next(
        item
        for item in modules
        if item.name == "module-loopback" and item.arg("source") == "vm_game.monitor"
    )
    health = collector.build_route_health(
        [*modules, duplicate],
        fixture_text("pw_link_vm_routes.txt"),
        {"vm_game", "vm_chat", "vm_music", "alsa_output.usb-Astro_Gaming_Astro_A50-00.stereo-chat"},
    )
    game = health["vm_game.monitor"]["alsa_output.usb-Astro_Gaming_Astro_A50-00.stereo-chat"]
    assert game.state is RouteState.UNKNOWN
    assert "ownership is ambiguous" in game.detail


def test_build_route_health_marks_missing_sink_unavailable_even_with_empty_graph(fixture_text):
    modules = collector.pactl.parse_modules(fixture_text("pactl_short_modules.txt"))
    health = collector.build_route_health(
        modules,
        "",
        {"vm_game", "vm_chat", "vm_music"},
    )
    game = health["vm_game.monitor"]["alsa_output.usb-Astro_Gaming_Astro_A50-00.stereo-chat"]
    assert game.state is RouteState.UNAVAILABLE


def test_build_route_health_marks_missing_sink_unavailable(fixture_text):
    modules = collector.pactl.parse_modules(fixture_text("pactl_short_modules.txt"))
    health = collector.build_route_health(
        modules,
        fixture_text("pw_link_vm_routes.txt"),
        {"vm_game", "vm_chat", "vm_music"},
    )
    game = health["vm_game.monitor"]["alsa_output.usb-Astro_Gaming_Astro_A50-00.stereo-chat"]
    assert game.state is RouteState.UNAVAILABLE


def test_collector_thread_delivers_and_stops(monkeypatch):
    seen: list[collector.Snapshot] = []
    got = threading.Event()

    def fake_collect(units):
        return collector.Snapshot(units=dict.fromkeys(units, "active"))

    monkeypatch.setattr(collector, "collect", fake_collect)
    worker = collector.Collector(["a.service"], lambda s: (seen.append(s), got.set()))
    worker.start()
    try:
        assert got.wait(timeout=2.0)
    finally:
        worker.stop()
    assert not worker.is_alive()
    assert seen[0].units == {"a.service": "active"}


def test_collector_survives_a_failing_tick(monkeypatch):
    calls = {"n": 0}
    recovered = threading.Event()

    def flaky(units):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("pactl went away")
        recovered.set()
        return collector.Snapshot()

    monkeypatch.setattr(collector, "collect", flaky)
    worker = collector.Collector([], lambda _s: None, interval=0.05)
    worker.start()
    try:
        assert recovered.wait(timeout=2.0), "collector died on the first failure"
    finally:
        worker.stop()


def test_visible_window_polls_faster():
    worker = collector.Collector([], lambda _s: None, interval=2.0)
    assert worker._delay() == collector.HIDDEN_MIN or worker._delay() == 2.0
    worker.set_window_visible(True)
    assert worker._delay() <= collector.VISIBLE_MAX
    worker.set_window_visible(False)
    assert worker._delay() >= collector.HIDDEN_MIN


@pytest.mark.skipif(os.name == "nt", reason="Linux-only PipeWire collector")
def test_source_output_query_failure_does_not_invalidate_graph(
    monkeypatch: pytest.MonkeyPatch, fixture_text
) -> None:
    outputs = {
        ("list", "sinks"): fixture_text("pactl_sinks.txt"),
        ("list", "sink-inputs"): fixture_text("pactl_sink_inputs.txt"),
        ("list", "short", "sources"): fixture_text("pactl_short_sources.txt"),
    }
    monkeypatch.setattr(collector.pactl, "raw_checked", lambda *args: outputs[args])
    modules = collector.pactl.parse_modules(fixture_text("pactl_short_modules.txt"))
    monkeypatch.setattr(collector.pactl, "modules", lambda **_kwargs: modules)

    def proc_run(argv, **_kwargs):
        if argv[:2] == ["pw-link", "-iol"]:
            output = fixture_text("pw_link_vm_routes.txt")
            return type("Result", (), {"stdout": output})()
        raise CommandError(argv, stderr="source-outputs timed out")

    monkeypatch.setattr(collector.proc, "run", proc_run)
    monkeypatch.setattr(collector.services, "states", lambda _units: {})
    monkeypatch.setattr(collector.pactl, "server_version", lambda: "")
    monkeypatch.setattr(collector.pactl, "default_sink", lambda: "")
    monkeypatch.setattr(
        collector,
        "_route_intent_snapshot",
        lambda: collector.RouteIntentSnapshot(
            microphone_status="unknown", vesktop_status="unavailable"
        ),
    )

    snapshot = collector.collect([])
    assert snapshot.graph is not None and snapshot.graph.status == "verified"
    assert snapshot.route_health is not None
    assert snapshot.capture_clients is not None
    assert snapshot.capture_clients.status == "unknown"
    assert "source-outputs timed out" in snapshot.capture_clients.detail


def test_route_intent_snapshot_keeps_configured_mic_routes_separate_from_vesktop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = tmp_path / "mic_routes.conf"
    config.write_text(
        'B1_ACTIVE="true"\n'
        'B1_ROUTE="sm7b"\n'
        'B2_ACTIVE="true"\n'
        'B2_ROUTE="astro"\n'
        'CARLA_TO_B1="true"\n'
        'CARLA_TO_B2="false"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(mic_routes, "CONF_PATH", config)
    monkeypatch.setattr(share, "selection_state", lambda: ("verified", ("game", "music")))

    snapshot = collector._route_intent_snapshot(taken_at=42.0)
    assert set(snapshot.microphone_edges) == {
        ("Carla", "mic_b1"),
        ("sm7b_mono", "mic_b1"),
        ("astro_mic_48k", "mic_b2"),
    }
    assert snapshot.microphone_status == "verified"
    assert snapshot.vesktop_targets == ("game", "music")
    assert snapshot.vesktop_status == "verified"
    assert snapshot.taken_at == 42.0
