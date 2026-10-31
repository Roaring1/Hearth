"""Native Windows Core Audio backend regressions.

These tests use endpoint/session reads only. Volume/mute setter dispatch is
verified with mocks so the user's live audio state is never changed by pytest.
"""

from __future__ import annotations

import os
import time

import pytest

from hearth import collector, meters, windows_audio
from hearth.audio import pactl
from hearth.ui.gtk4.widgets import VMAX

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows Core Audio backend")


def test_collector_maps_the_six_hearth_strips_to_active_endpoints() -> None:
    snapshot = collector.collect(())
    assert snapshot.server_version == "Windows Core Audio"
    assert snapshot.routes is None
    assert snapshot.units == {}
    assert snapshot.default_sink

    expected = {
        "vm_game",
        "vm_chat",
        "vm_music",
        "laptop_audio",
        "mic_b1",
        "mic_b2",
    }
    assert expected.issubset(snapshot.sinks)
    for key in expected:
        state = snapshot.sinks[key]
        assert state.name == key
        assert state.state == "RUNNING"
        assert 0 <= state.volume <= 100


def test_default_sink_tracks_the_current_windows_default_endpoint() -> None:
    snapshot = collector.collect(())
    assert windows_audio.default_sink() == snapshot.default_sink
    assert (
        snapshot.default_sink
        in {
            "vm_game",
            "vm_chat",
            "vm_music",
            "laptop_audio",
        }
        or snapshot.default_sink
    )


def test_pactl_compatibility_inventories_are_backed_by_core_audio() -> None:
    sinks = pactl.sinks()
    sources = pactl.sources()
    assert {entry.name for entry in sinks} >= {"vm_game", "vm_chat", "vm_music", "laptop_audio"}
    assert {entry.name for entry in sources} >= {"mic_b1", "mic_b2"}
    assert all(entry.driver == "Windows Core Audio" for entry in sinks + sources)
    assert pactl.server_version() == "Windows Core Audio"


def test_endpoint_and_peak_reads_are_bounded_and_read_only() -> None:
    snapshot = collector.collect(())
    for key in snapshot.sinks:
        state = windows_audio.get_endpoint_state(key)
        assert state is not None
        volume, muted = state
        assert 0 <= volume <= 100
        assert isinstance(muted, bool)

    sources = [key + ".monitor" for key in snapshot.sinks]
    levels = windows_audio.peaks(sources)
    assert set(levels) == set(sources)
    assert all(0.0 <= value <= 1.0 for value in levels.values())


def test_meter_worker_publishes_core_audio_peak_levels_and_stops() -> None:
    snapshot = collector.collect(())
    sources = [key + ".monitor" for key in snapshot.sinks]
    poller = meters.PeakPoller(sources=sources)
    poller.start()
    deadline = time.monotonic() + 5.0
    try:
        while time.monotonic() < deadline and not poller.levels():
            time.sleep(0.05)
        levels = poller.levels()
        assert set(levels) == set(sources)
        assert all(0.0 <= value <= 1.0 for value in levels.values())
    finally:
        poller.stop(timeout=2.0)
    assert not poller.is_alive()


def test_pactl_volume_mute_dispatch_uses_core_audio_setters_without_touching_devices(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(
        windows_audio,
        "set_endpoint_volume",
        lambda name, percent: calls.append(("endpoint-volume", name, percent)) or True,
    )
    monkeypatch.setattr(
        windows_audio,
        "set_endpoint_mute",
        lambda name, muted: calls.append(("endpoint-mute", name, muted)) or True,
    )
    monkeypatch.setattr(
        windows_audio,
        "set_session_volume",
        lambda index, percent: calls.append(("session-volume", index, percent)) or True,
    )
    monkeypatch.setattr(
        windows_audio,
        "set_session_mute",
        lambda index, muted: calls.append(("session-mute", index, muted)) or True,
    )

    assert pactl.set_volume("vm_game", 73)
    assert pactl.set_mute("vm_chat", True)
    assert pactl.set_stream_volume("device-id|session-id", 125)
    assert pactl.set_stream_mute("device-id|session-id", False)
    assert calls == [
        ("endpoint-volume", "vm_game", 73),
        ("endpoint-mute", "vm_chat", True),
        ("session-volume", "device-id|session-id", 125),
        ("session-mute", "device-id|session-id", False),
    ]


def test_windows_route_operations_fail_closed() -> None:
    assert windows_audio.routing_supported() is False
    assert pactl.modules() == []
    assert pactl.set_default_sink("vm_game") is False
    assert pactl.move_sink_input("some-session", "vm_chat") is False
    assert pactl.unload_loopbacks("some-source") == 0
    with pytest.raises(NotImplementedError):
        pactl.load_loopback("some-source", "vm_game")


def test_core_audio_session_volume_never_exceeds_native_scalar_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[float] = []

    class FakeSimpleVolume:
        def SetMasterVolume(self, value, _context) -> None:
            calls.append(float(value))

    class FakeSession:
        SimpleAudioVolume = FakeSimpleVolume()

    monkeypatch.setattr(windows_audio, "_find_session", lambda _key: FakeSession())

    assert windows_audio.set_session_volume("fake-endpoint|fake-session", 150)
    assert calls == [1.0]
    assert windows_audio.set_session_volume("fake-endpoint|fake-session", -20)
    assert calls == [1.0, 0.0]


def test_meter_worker_removes_stale_levels_when_a_source_disappears(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        windows_audio,
        "peaks",
        lambda sources: dict.fromkeys(sources, 0.5),
    )
    poller = meters.PeakPoller(sources=["temporary.monitor"])
    poller.start()
    deadline = time.monotonic() + 3.0
    try:
        while time.monotonic() < deadline and not poller.levels():
            time.sleep(0.02)
        assert poller.levels() == {"temporary.monitor": 0.5}
        poller.set_sources([])
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and poller.levels():
            time.sleep(0.02)
        assert poller.levels() == {}
    finally:
        poller.stop(timeout=2.0)
    assert not poller.is_alive()


def test_collector_limits_app_session_snapshot_volume_to_core_audio_range() -> None:
    snapshot = collector.collect(())
    for entries in snapshot.streams.values():
        for session in entries:
            assert 0 <= session.volume <= 100


def test_windows_gtk_endpoint_fader_uses_core_audio_range() -> None:
    assert VMAX == 100
