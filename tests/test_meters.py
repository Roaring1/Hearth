"""Meter tests: RMS maths, framing edge cases, and the parec contract."""

from __future__ import annotations

import math
import struct

from hearth import meters


def _frames(samples):
    return struct.pack(f"<{len(samples)}h", *samples)


def test_silence_is_zero():
    assert meters.rms_peak(_frames([0] * 64)) == (0.0, 0.0)


def test_full_scale_is_one():
    rms, peak = meters.rms_peak(_frames([32767, -32767] * 32))
    assert math.isclose(rms, 1.0, rel_tol=1e-3)
    assert math.isclose(peak, 1.0, rel_tol=1e-3)


def test_peak_tracks_the_loudest_sample_including_negatives():
    _rms, peak = meters.rms_peak(_frames([0, 0, -16384, 100]))
    assert math.isclose(peak, 0.5, rel_tol=1e-3)


def test_rms_of_a_half_scale_square_wave():
    rms, _peak = meters.rms_peak(_frames([16384, -16384] * 16))
    assert math.isclose(rms, 0.5, rel_tol=1e-3)


def test_odd_trailing_byte_is_ignored_not_fatal():
    """A read can land mid-frame; the leftover byte must not raise."""
    rms, peak = meters.rms_peak(_frames([16384, 16384]) + b"\x01")
    assert rms > 0 and peak > 0


def test_empty_and_single_byte_input():
    assert meters.rms_peak(b"") == (0.0, 0.0)
    assert meters.rms_peak(b"\x00") == (0.0, 0.0)


def test_dbfs_scale():
    assert meters.dbfs(1.0) == 0.0
    assert math.isclose(meters.dbfs(0.5), -6.02, abs_tol=0.05)
    assert meters.dbfs(0.0) == meters.SILENCE_DBFS
    assert meters.dbfs(1e-12) == meters.SILENCE_DBFS


def test_parec_argv_hides_the_stream_from_the_plasma_applet():
    argv = meters.parec_argv("vm_game.monitor")
    assert argv[0] == "parec"
    assert "--device=vm_game.monitor" in argv
    assert "--format=s16le" in argv
    assert f"--rate={meters.RATE}" in argv
    assert "--property=media.category=Monitor" in argv
    assert "--property=stream.dont-record=1" in argv
    assert any(a.startswith("--property=application.id=") for a in argv)


def test_parec_argv_latency_is_an_int():
    assert "--latency-msec=45" in meters.parec_argv("x.monitor", latency_msec=45)


def test_poller_reports_levels_and_stops_cleanly(monkeypatch):
    monkeypatch.setattr(meters.proc, "have", lambda _p: False)
    poller = meters.PeakPoller(["vm_game.monitor"])
    assert poller.available is False
    assert poller.level("vm_game.monitor") == 0.0
    assert poller.levels() == {}

    poller.start()
    poller.stop()
    assert not poller.is_alive()


def test_set_sources_is_thread_safe_to_call_before_start():
    poller = meters.PeakPoller()
    poller.set_sources(["a.monitor", "b.monitor"])
    assert poller._sources == ["a.monitor", "b.monitor"]


def test_cooldown_prevents_a_respawn_loop(monkeypatch):
    """A source that cannot start must not be respawned on every pass."""
    starts = {"n": 0}

    def boom(_argv, **_kwargs):
        starts["n"] += 1
        raise meters.CommandError(["parec"], stderr="no such device")

    monkeypatch.setattr(meters.proc, "stream", boom)
    poller = meters.PeakPoller(["ghost.monitor"])
    poller._ensure("ghost.monitor", 100.0)
    poller._ensure("ghost.monitor", 100.5)  # inside the cooldown
    assert starts["n"] == 1
    poller._ensure("ghost.monitor", 100.0 + meters.RESTART_COOLDOWN + 0.1)
    assert starts["n"] == 2
