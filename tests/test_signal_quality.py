"""Deterministic replay tests for the measurement-only signal-quality core."""

import math

import pytest

from hearth.audio.signal_quality import (
    SignalState,
    TransportSnapshot,
    TransportState,
    analyze_pcm,
    compare_taps,
    compare_transport,
)


@pytest.fixture
def quiet_pcm():
    return [0.0] * 1024


def tone(freq: float, rate: int = 48000, count: int = 2048, amp: float = 0.2):
    return [amp * math.sin(2.0 * math.pi * freq * i / rate) for i in range(count)]


def test_replay_silence_is_no_sample_not_a_fault(quiet_pcm):
    report = analyze_pcm(quiet_pcm, 48000)
    assert report.state is SignalState.NO_SAMPLE
    assert report.metrics is not None
    assert report.metrics.rms == 0.0
    assert "silence is not a quality verdict" in report.limitations


def test_replay_clean_tone_reports_basic_measurements():
    report = analyze_pcm(tone(440), 48000)
    assert report.state is SignalState.HEALTHY_OBSERVED
    assert report.metrics is not None
    assert report.metrics.rms == pytest.approx(0.2 / math.sqrt(2), rel=0.02)
    assert report.metrics.peak == pytest.approx(0.2, rel=0.01)
    assert report.metrics.crest_factor_db == pytest.approx(3.01, abs=0.08)
    assert report.metrics.clipped_sample_count == 0


def test_replay_clipping_is_candidate_not_confirmed_fault():
    samples = tone(220, amp=1.0)
    report = analyze_pcm(samples, 48000)
    assert report.state is SignalState.SUSPECT
    assert report.metrics is not None
    assert report.metrics.clipped_sample_count > 0
    assert report.metrics.clipped_runs > 0
    assert "clipping candidate" in report.reasons[0]


def test_replay_impulse_emits_bounded_transient_candidate():
    samples = [0.0] * 2048
    samples[800] = 0.8
    report = analyze_pcm(samples, 48000)
    assert report.state is SignalState.SUSPECT
    assert report.metrics is not None
    assert report.metrics.transient_candidates >= 1
    assert 800 in report.metrics.transient_candidate_indices
    assert report.metrics.transient_candidates <= 32


def test_replay_nonfinite_sample_is_inconclusive():
    samples = tone(330)
    samples[100] = float("nan")
    report = analyze_pcm(samples, 48000)
    assert report.state is SignalState.INCONCLUSIVE
    assert report.metrics is None
    assert "non-finite" in report.reasons[0]


def test_invalid_rate_short_and_oversized_buffers_fail_closed():
    assert analyze_pcm(tone(100), 0).state is SignalState.INCONCLUSIVE
    assert analyze_pcm([0.0] * 20, 48000).state is SignalState.INCONCLUSIVE
    oversized = analyze_pcm([0.0] * 480_001, 48000)
    assert oversized.state is SignalState.INCONCLUSIVE
    assert "bound" in oversized.reasons[0]


def test_raw_post_compare_localizes_only_candidate_categories():
    raw = analyze_pcm(tone(200, amp=1.0), 48000)
    clean = analyze_pcm(tone(200, amp=0.2), 48000)
    result = compare_taps(raw, clean)
    assert result.localization == "RAW_ONLY"
    assert result.rms_ratio == pytest.approx(0.2)
    assert result.confidence == "low"
    assert "not validated" in result.reasons[0]


def test_tap_comparison_rejects_mismatched_coverage():
    raw = analyze_pcm(tone(200, count=1024), 48000)
    post = analyze_pcm(tone(200, count=2048), 48000)
    result = compare_taps(raw, post)
    assert result.localization == "UNLOCALIZED"
    assert result.confidence == "low"


def test_transport_err_delta_requires_same_node_and_nonreset_counter():
    first = TransportSnapshot("node-7", 12, 48000, 256, "driver")
    next_ok = TransportSnapshot("node-7", 15, 48000, 256, "driver")
    delta = compare_transport(first, next_ok)
    assert delta.state is TransportState.VERIFIED
    assert delta.err_delta == 3
    assert delta.sample_rate_changed is False
    assert delta.quantum_changed is False

    replaced = compare_transport(first, TransportSnapshot("node-8", 15, 48000, 256, "driver"))
    assert replaced.state is TransportState.NODE_CHANGED
    assert replaced.err_delta is None

    reset = compare_transport(first, TransportSnapshot("node-7", 2, 48000, 256, "driver"))
    assert reset.state is TransportState.COUNTER_RESET
    assert reset.err_delta is None


def test_transport_missing_and_invalid_values_are_unknown():
    assert compare_transport(None, TransportSnapshot("n", 1)).state is TransportState.UNKNOWN
    bad = compare_transport(TransportSnapshot("n", -1), TransportSnapshot("n", 1))
    assert bad.state is TransportState.UNKNOWN
    assert bad.err_delta is None

    role = compare_transport(
        TransportSnapshot("n", 2, role="driver"), TransportSnapshot("n", 3, role="follower")
    )
    assert role.state is TransportState.UNKNOWN


def test_transport_metadata_changes_are_separate_from_err_count():
    first = TransportSnapshot("n", 4, 48000, 256)
    second = TransportSnapshot("n", 4, 44100, 512)
    result = compare_transport(first, second)
    assert result.state is TransportState.VERIFIED
    assert result.err_delta == 0
    assert result.sample_rate_changed is True
    assert result.quantum_changed is True


def test_invalid_config_and_boolean_sample_rate_fail_closed():
    from hearth.audio.signal_quality import SignalConfig

    invalid = analyze_pcm(tone(100), 48000, SignalConfig(clip_threshold=0.0))
    assert invalid.state is SignalState.INCONCLUSIVE
    assert "configuration" in invalid.reasons[0]
    assert analyze_pcm(tone(100), True).state is SignalState.INCONCLUSIVE


def test_transport_boolean_and_float_counters_fail_closed():
    bool_counter = compare_transport(TransportSnapshot("n", True), TransportSnapshot("n", 2))
    assert bool_counter.state is TransportState.UNKNOWN
    float_counter = compare_transport(TransportSnapshot("n", 1.2), TransportSnapshot("n", 2))
    assert float_counter.state is TransportState.UNKNOWN
    float_rate = compare_transport(
        TransportSnapshot("n", 1, 48000.5), TransportSnapshot("n", 2, 48000)
    )
    assert float_rate.state is TransportState.UNKNOWN
