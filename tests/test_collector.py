"""Collector tests, run against output recorded from the live rig."""

from __future__ import annotations

import threading

import pytest

from hearth import collector


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
