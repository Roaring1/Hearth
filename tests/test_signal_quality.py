"""Deterministic replay tests for the measurement-only signal-quality core."""

import math

import pytest

from hearth.audio.signal_quality import (
    DropoutConfig,
    DropoutState,
    SignalState,
    TransportContextState,
    TransportSnapshot,
    TransportState,
    analyze_pcm,
    analyze_stereo_pcm,
    compare_taps,
    compare_transport,
    detect_dropouts,
    detect_transport_context,
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


@pytest.mark.parametrize(
    "field",
    (
        "clip_threshold",
        "near_zero_threshold",
        "transient_mad_multiplier",
        "transient_floor",
    ),
)
def test_boolean_config_thresholds_are_rejected(field):
    from hearth.audio.signal_quality import SignalConfig

    report = analyze_pcm(tone(100), 48000, SignalConfig(**{field: True}))
    assert report.state is SignalState.INCONCLUSIVE
    assert "configuration" in report.reasons[0]


def test_direct_stable_tone_detector_rejects_boolean_sample_rate():
    from hearth.audio.signal_quality import detect_stable_tone

    assert detect_stable_tone(tone(100, count=1024), True).classification == "INCONCLUSIVE"


def test_transport_metadata_boolean_sample_rate_and_quantum_are_rejected():
    for before, after in (
        (TransportSnapshot("n", 1, True, 256), TransportSnapshot("n", 2, 48000, 256)),
        (TransportSnapshot("n", 1, 48000, True), TransportSnapshot("n", 2, 48000, 256)),
        (TransportSnapshot("n", 1, 48000, 256), TransportSnapshot("n", 2, True, 256)),
        (TransportSnapshot("n", 1, 48000, 256), TransportSnapshot("n", 2, 48000, True)),
    ):
        result = compare_transport(before, after)
        assert result.state is TransportState.UNKNOWN
        assert result.err_delta is None


def test_config_requires_finite_thresholds_and_positive_floor():
    from hearth.audio.signal_quality import SignalConfig

    assert (
        analyze_pcm(tone(100), 48000, SignalConfig(transient_floor=0)).state
        is SignalState.INCONCLUSIVE
    )
    assert (
        analyze_pcm(tone(100), 48000, SignalConfig(near_zero_threshold=1.0)).state
        is SignalState.INCONCLUSIVE
    )
    assert (
        analyze_pcm(tone(100), 48000, SignalConfig(transient_mad_multiplier=float("inf"))).state
        is SignalState.INCONCLUSIVE
    )
    assert (
        analyze_pcm(tone(100), 48000, SignalConfig(max_samples=1024.5)).state
        is SignalState.INCONCLUSIVE
    )


def test_replay_97_to_99_hz_line_is_unidentified_tone_not_mains_label():
    report = analyze_pcm(tone(98.0, count=48000, amp=0.03), 48000)
    assert report.metrics is not None
    detected = report.metrics.stable_tone
    assert detected.classification == "UNIDENTIFIED_TONE"
    assert detected.frequency_hz == pytest.approx(98.0, abs=0.2)
    assert detected.confidence == "low"
    assert "mains" not in detected.classification.lower()
    assert "hum" not in detected.classification.lower()


def test_replay_stereo_identical_and_inverted_phase_metrics():
    left = tone(1000.0, count=8192, amp=0.2)
    same = analyze_stereo_pcm(left, left, 48000)
    assert same.metrics is not None
    assert same.metrics.correlation == pytest.approx(1.0, abs=1e-6)
    assert same.metrics.phase_difference_degrees == pytest.approx(0.0, abs=0.2)
    inverted = analyze_stereo_pcm(left, [-sample for sample in left], 48000)
    assert inverted.metrics is not None
    assert inverted.metrics.correlation == pytest.approx(-1.0, abs=1e-6)
    assert abs(inverted.metrics.phase_difference_degrees) == pytest.approx(180.0, abs=0.2)


def test_replay_stereo_quadrature_phase_and_true_peak_estimates():
    left = tone(1000.0, count=8192, amp=0.2)
    right = [0.2 * math.cos(2.0 * math.pi * 1000.0 * i / 48000) for i in range(len(left))]
    report = analyze_stereo_pcm(left, right, 48000)
    assert report.metrics is not None
    assert report.metrics.correlation == pytest.approx(0.0, abs=0.01)
    assert report.metrics.phase_difference_degrees == pytest.approx(90.0, abs=0.5)
    assert report.metrics.left_true_peak >= report.left.metrics.peak
    assert report.metrics.right_true_peak >= report.right.metrics.peak


def test_replay_cubic_true_peak_estimate_catches_inter_sample_overshoot():
    samples = [-1.0, 0.9, 0.9, -1.0] + [0.0] * 1020
    report = analyze_pcm(samples, 48000)
    assert report.metrics is not None
    assert report.metrics.peak == 1.0
    assert report.metrics.true_peak_estimate > report.metrics.peak


def test_stereo_mismatched_buffers_fail_closed():
    report = analyze_stereo_pcm(tone(220, count=2048), tone(220, count=1024), 48000)
    assert report.metrics is None
    assert "unequal lengths" in report.reasons[0]


def test_replay_detects_sustained_dropout_candidate_not_short_quiet_window():
    from hearth.audio.signal_quality import DropoutConfig

    samples = [0.2] * 200
    samples[60:140] = [0.0] * 80  # 80 ms gap at 1 kHz
    report = detect_dropouts(samples, 1000, DropoutConfig(window_ms=20, min_windows=3))
    assert report.state is DropoutState.CANDIDATE
    assert report.dropout_start_samples == (60,)
    assert report.dropout_windows == 4
    assert report.longest_dropout_windows == 4
    assert "not confirmed faults" in report.limitations[0]


def test_replay_short_silence_is_not_a_dropout_candidate():
    samples = [0.2] * 200
    samples[80:100] = [0.0] * 20
    report = detect_dropouts(samples, 1000, DropoutConfig(window_ms=20, min_windows=3))
    assert report.state is DropoutState.NO_CANDIDATE
    assert report.dropout_start_samples == ()


def test_dropout_detector_fails_closed_for_invalid_and_unbounded_input():
    assert detect_dropouts([0.0] * 100, True).state is DropoutState.INCONCLUSIVE
    assert detect_dropouts([0.0] * 481_000, 48000).state is DropoutState.INCONCLUSIVE
    assert detect_dropouts([float("nan")] * 100, 48000).state is DropoutState.INCONCLUSIVE
    assert (
        detect_dropouts([0.0] * 100, 48000, DropoutConfig(window_ms=True)).state
        is DropoutState.INCONCLUSIVE
    )


def test_replay_transport_context_stable_changed_and_unknown():
    baseline = TransportSnapshot("node-7", 12, 48000, 256, "driver")
    stable = detect_transport_context(
        baseline, TransportSnapshot("node-7", 12, 48000, 256, "driver")
    )
    assert stable.state is TransportContextState.STABLE
    assert stable.changed_fields == ()

    rate_changed = detect_transport_context(
        baseline, TransportSnapshot("node-7", 12, 44100, 256, "driver")
    )
    assert rate_changed.state is TransportContextState.CHANGED
    assert "sample_rate" in rate_changed.changed_fields

    errors_changed = detect_transport_context(
        baseline, TransportSnapshot("node-7", 15, 48000, 256, "driver")
    )
    assert errors_changed.state is TransportContextState.CHANGED
    assert errors_changed.err_delta == 3
    assert "err_count" in errors_changed.changed_fields

    node_changed = detect_transport_context(
        baseline, TransportSnapshot("node-8", 15, 48000, 256, "driver")
    )
    assert node_changed.state is TransportContextState.CHANGED
    assert node_changed.changed_fields == ("node_id",)
    missing = detect_transport_context(None, baseline)
    assert missing.state is TransportContextState.UNKNOWN
