"""Synthetic tests for bounded microphone signal features."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

np = pytest.importorskip("numpy", reason="signal diagnostics require the optional NumPy dependency")

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from audio_health_signal import analyze_signal_features  # noqa: E402


def _noise(seed: int, seconds: float = 8.0, sample_rate: int = 48_000) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.normal(0.0, 0.00005, int(seconds * sample_rate))


def test_detects_arbitrary_persistent_97_5_hz_line_without_calling_it_mains_hum() -> None:
    rate = 48_000
    time = np.arange(8 * rate, dtype=np.float64) / rate
    tone = 0.01 * np.sin(2 * np.pi * 97.5 * time)
    noise = _noise(7)
    result = analyze_signal_features(tone + noise, tone + noise * 0.8, rate)
    lines = result["raw"]["persistent_tones"]
    assert lines
    assert abs(lines[0]["frequency_hz"] - 97.5) <= 0.6
    assert lines[0]["persistence"] >= 0.6
    assert result["tone_localization"] == "present_raw_and_processed"
    assert "mains" not in result["interpretation"]
    assert result["automatic_action"] == "none"


def test_broadband_noise_estimate_tracks_low_energy_level_without_fixed_verdict() -> None:
    low = _noise(10)
    rng = np.random.default_rng(11)
    elevated = rng.normal(0.0, 0.002, low.size)
    result_low = analyze_signal_features(low, low)
    result_high = analyze_signal_features(elevated, elevated)
    low_db = result_low["raw"]["low_energy"]["rms_dbfs"]
    high_db = result_high["raw"]["low_energy"]["rms_dbfs"]
    assert low_db is not None and high_db is not None
    assert high_db > low_db + 15.0
    assert result_high["interpretation"].startswith("measurement_only")


def test_raw_only_tone_is_localized_before_processing() -> None:
    rate = 48_000
    time = np.arange(8 * rate, dtype=np.float64) / rate
    raw = _noise(17) + 0.008 * np.sin(2 * np.pi * 123.25 * time)
    processed = _noise(18)
    result = analyze_signal_features(raw, processed, rate)
    assert result["tone_localization"] == "present_raw_only"
    assert abs(result["raw"]["persistent_tones"][0]["frequency_hz"] - 123.25) <= 0.6


def test_no_persistent_tone_is_returned_for_broadband_white_noise() -> None:
    noise = _noise(20)
    result = analyze_signal_features(noise, noise)
    assert result["raw"]["persistent_tones"] == []
    assert result["tone_localization"] == "no_persistent_line_detected"


def test_low_energy_metrics_are_reported_separately_from_speech_activity() -> None:
    rate = 48_000
    rng = np.random.default_rng(22)
    samples = _noise(23)
    # Add four short speech-like bursts; the lower energy quartile should still
    # be reported as an estimate, without pretending the frames were VAD-labelled.
    for start in (1, 5):
        offset = start * rate
        samples[offset : offset + rate // 4] += rng.normal(0.0, 0.003, rate // 4)
    result = analyze_signal_features(samples, samples, rate)
    assert result["raw"]["low_energy"]["frames"] >= 3
    assert result["raw"]["low_energy"]["rms_dbfs"] is not None
    assert result["raw"]["low_energy"]["5k_12k_flatness"] is not None


def test_voice_only_tone_bursts_are_excluded_by_low_energy_window_selection() -> None:
    rate = 48_000
    samples = _noise(32)
    time = np.arange(rate // 3, dtype=np.float64) / rate
    voiced = (
        0.01 * np.sin(2 * np.pi * 110.0 * time)
        + 0.004 * np.sin(2 * np.pi * 220.0 * time)
        + 0.002 * np.sin(2 * np.pi * 330.0 * time)
    )
    for start in (0, 4):
        offset = start * rate
        samples[offset : offset + voiced.size] += voiced
    result = analyze_signal_features(samples, samples, rate)
    assert result["raw"]["persistent_tones"] == []


def test_persistent_tone_remains_detectable_during_low_energy_background_windows() -> None:
    rate = 48_000
    time = np.arange(8 * rate, dtype=np.float64) / rate
    background_line = 0.006 * np.sin(2 * np.pi * 97.5 * time)
    rng = np.random.default_rng(33)
    samples = background_line + rng.normal(0.0, 0.00005, background_line.size)
    voiced_time = np.arange(rate // 4, dtype=np.float64) / rate
    voiced = sum(
        amplitude * np.sin(2 * np.pi * frequency * voiced_time)
        for frequency, amplitude in ((120, 0.01), (240, 0.004), (360, 0.002))
    )
    for start in (1, 3, 5, 7):
        offset = start * rate
        samples[offset : offset + voiced.size] += voiced
    result = analyze_signal_features(samples, samples, rate)
    assert result["raw"]["persistent_tones"]
    assert any(
        abs(line["frequency_hz"] - 97.5) <= 0.6 for line in result["raw"]["persistent_tones"]
    )


def test_repeated_high_frequency_impulses_raise_transient_descriptors() -> None:
    rate = 48_000
    rng = np.random.default_rng(41)
    clean = rng.normal(0.0, 0.0001, 8 * rate)
    damaged = clean.copy()
    impulse = 0.15 * np.sin(2 * np.pi * 9000 * np.arange(96) / rate)
    for start in (1, 2, 3, 4, 5, 6):
        offset = start * rate + rate // 3
        damaged[offset : offset + impulse.size] += impulse

    clean_features = analyze_signal_features(clean, clean)["raw"]["transients"]
    damaged_features = analyze_signal_features(damaged, damaged)["raw"]["transients"]
    assert damaged_features["hf_excess_kurtosis"] > clean_features["hf_excess_kurtosis"]
    assert damaged_features["transient_frames"] > clean_features["transient_frames"]
    assert damaged_features["sample_peak_dbfs"] > clean_features["sample_peak_dbfs"]


def test_sample_peak_is_not_reported_as_clipping_when_below_threshold() -> None:
    samples = np.full(8 * 48_000, 0.5, dtype=np.float64)
    metrics = analyze_signal_features(samples, samples)["raw"]["transients"]
    assert metrics["sample_peak_dbfs"] < -5.0
    assert metrics["near_clip_fraction"] == 0.0


def test_raw_to_processed_transient_deltas_localize_added_processing_impulses() -> None:
    rate = 48_000
    rng = np.random.default_rng(51)
    raw = rng.normal(0.0, 0.0001, 8 * rate)
    processed = raw.copy()
    impulse = 0.12 * np.sin(2 * np.pi * 8500 * np.arange(120) / rate)
    for start in (1, 3, 5, 7):
        offset = start * rate + rate // 2
        processed[offset : offset + impulse.size] += impulse
    result = analyze_signal_features(raw, processed, rate)
    assert result["raw_to_processed"]["hf_excess_kurtosis_delta"] > 0
    assert result["raw_to_processed"]["transient_frame_delta"] >= 0
    assert result["automatic_action"] == "none"


def test_swept_or_drifting_tone_is_not_classified_as_a_persistent_stable_line() -> None:
    rate = 48_000
    samples = _noise(62)
    time = np.arange(8 * rate, dtype=np.float64) / rate
    # Continuous 93 -> 102 Hz chirp, with phase integrated so there are no
    # artificial discontinuities at analysis-frame boundaries.
    phase = 2 * np.pi * (93.0 * time + 0.5 * (9.0 / 8.0) * time * time)
    samples += 0.006 * np.sin(phase)
    result = analyze_signal_features(samples, samples, rate)
    assert result["raw"]["persistent_tones"] == []


def test_integrity_metrics_report_nonfinite_samples_and_long_zero_runs() -> None:
    rate = 48_000
    samples = _noise(71)
    samples[rate : rate + 120] = 0.0  # 2.5 ms exact digital-zero run
    samples[2 * rate] = np.nan
    samples[2 * rate + 1] = np.inf
    result = analyze_signal_features(samples, samples, rate)
    raw = result["integrity"]["raw"]
    assert raw["nonfinite_samples"] == 2
    assert raw["exact_zero_runs_ge_1ms"] == 1
    assert raw["max_exact_zero_run_ms"] >= 2.4


def test_integrity_metrics_detect_repeated_nonzero_and_full_scale_plateaus() -> None:
    rate = 48_000
    samples = _noise(72)
    samples[rate : rate + 64] = 0.25
    samples[2 * rate : 2 * rate + 8] = 1.0
    result = analyze_signal_features(samples, samples, rate)
    raw = result["integrity"]["raw"]
    assert raw["max_repeated_nonzero_run_ms"] >= 1.3
    assert raw["full_scale_samples"] == 8
    assert raw["full_scale_runs_ge_2_samples"] == 1
    assert raw["max_full_scale_run_ms"] >= 0.16


def test_intentional_digital_silence_is_reported_as_measurement_not_fault_verdict() -> None:
    silence = np.zeros(8 * 48_000, dtype=np.float64)
    result = analyze_signal_features(silence, silence)
    assert result["integrity"]["raw"]["exact_zero_runs_ge_1ms"] == 1
    assert result["integrity"]["raw"]["max_exact_zero_run_ms"] >= 7999.0
    assert result["automatic_action"] == "none"


def test_nonfinite_sample_breaks_zero_run_instead_of_being_compacted() -> None:
    rate = 48_000
    samples = _noise(73)
    samples[rate : rate + 64] = 0.0
    samples[rate + 64] = np.nan
    samples[rate + 65 : rate + 129] = 0.0
    integrity = analyze_signal_features(samples, samples, rate)["integrity"]["raw"]
    assert integrity["nonfinite_samples"] == 1
    assert integrity["exact_zero_runs_ge_1ms"] == 2
    assert integrity["max_exact_zero_run_ms"] >= 1.3


def test_band_level_deltas_expose_processing_chain_high_frequency_attenuation() -> None:
    rate = 48_000
    time = np.arange(8 * rate, dtype=np.float64) / rate
    low = 0.01 * np.sin(2 * np.pi * 300.0 * time)
    high = 0.01 * np.sin(2 * np.pi * 7000.0 * time)
    raw = low + high
    processed = low + 0.25 * high
    result = analyze_signal_features(raw, processed, rate)
    deltas = result["raw_to_processed"]["band_level_delta_db"]
    assert abs(deltas["250_500_hz"]) < 0.5
    assert deltas["5k_12k_hz"] < -10.0


def test_signal_feature_summary_is_json_safe_and_contains_no_waveform() -> None:
    import json

    samples = _noise(81)
    result = analyze_signal_features(samples, samples)
    encoded = json.dumps(result, allow_nan=False)
    assert "automatic_action" in result
    assert "samples" not in result
    assert len(encoded) < 24_000
    assert "nan" not in encoded.lower()


def test_signal_feature_analysis_rejects_invalid_sample_rate() -> None:
    samples = _noise(82, seconds=1.0)
    try:
        analyze_signal_features(samples, samples, sample_rate=0)
    except ValueError as exc:
        assert "sample_rate" in str(exc)
    else:
        raise AssertionError("invalid sample rate must be rejected")


def test_multicue_impulsive_candidate_avoids_near_silence_kurtosis_false_positive() -> None:
    rate = 48_000
    rng = np.random.default_rng(91)
    clean = rng.normal(0.0, 0.0001, 8 * rate)
    damaged = clean.copy()
    impulse = 0.15 * np.sin(2 * np.pi * 9000 * np.arange(96) / rate)
    for start in (1, 2, 3, 4, 5, 6):
        offset = start * rate + rate // 3
        damaged[offset : offset + impulse.size] += impulse

    clean_result = analyze_signal_features(clean, clean, rate)
    damaged_result = analyze_signal_features(damaged, damaged, rate)
    assert clean_result["raw"]["impulsive_event_candidate"]["candidate"] is False
    assert damaged_result["raw"]["impulsive_event_candidate"]["candidate"] is True
    assert damaged_result["impulse_localization"] == "both_points_candidate"
    assert damaged_result["raw"]["impulsive_event_candidate"]["automatic_action"] == "none"

    quiet = np.zeros(8 * rate, dtype=np.float64)
    quiet[100] = 0.1
    quiet_result = analyze_signal_features(quiet, quiet, rate)
    quiet_candidate = quiet_result["raw"]["impulsive_event_candidate"]
    assert quiet_candidate["candidate"] is False
    assert quiet_candidate["cues"]["high_frequency_activity"] is False


def test_broadband_impulse_detector_finds_short_multi_band_clicks() -> None:
    rate = 48_000
    rng = np.random.default_rng(91)
    samples = rng.normal(0.0, 0.00002, 8 * rate)
    impulse_samples = int(rate * 0.0008)
    envelope = np.exp(-np.arange(impulse_samples) / (rate * 0.00025))
    impulse = 0.15 * envelope * rng.normal(size=impulse_samples)
    expected = (1, 3, 5, 7)
    for second in expected:
        start = second * rate + rate // 3
        samples[start : start + impulse_samples] += impulse
    metrics = analyze_signal_features(samples, samples, rate)["raw"]["broadband_impulses"]
    assert metrics["candidate_count"] >= 3
    detected = [item["start_seconds"] for item in metrics["candidate_events"]]
    assert all(
        any(abs(time - (second + 1 / 3)) <= 0.03 for time in detected) for second in expected
    )


def test_broadband_impulse_detector_rejects_long_high_frequency_sibilance() -> None:
    rate = 48_000
    rng = np.random.default_rng(92)
    samples = _noise(93)
    for second in (1, 3, 5, 7):
        start = second * rate
        width = int(rate * 0.12)
        samples[start : start + width] += rng.normal(0.0, 0.002, width)
    metrics = analyze_signal_features(samples, samples, rate)["raw"]["broadband_impulses"]
    assert metrics["candidate_count"] == 0


def test_broadband_impulse_metrics_localize_processing_only_impulses() -> None:
    rate = 48_000
    raw = _noise(94)
    processed = raw.copy()
    rng = np.random.default_rng(95)
    impulse = 0.12 * rng.normal(size=int(rate * 0.001))
    for second in (1, 3, 5, 7):
        start = second * rate + rate // 2
        processed[start : start + impulse.size] += impulse
    features = analyze_signal_features(raw, processed, rate)
    assert (
        features["processed"]["broadband_impulses"]["candidate_count"]
        > (features["raw"]["broadband_impulses"]["candidate_count"])
    )
    assert features["raw_to_processed"]["broadband_impulse_candidate_delta"] > 0


def test_broadband_impulse_event_list_is_bounded_while_total_count_is_retained() -> None:
    rate = 48_000
    rng = np.random.default_rng(96)
    samples = rng.normal(0.0, 0.00002, 8 * rate)
    impulse = 0.15 * rng.normal(size=int(rate * 0.0008))
    for start_ms in range(200, 7800, 150):
        offset = int(start_ms * rate / 1000)
        samples[offset : offset + impulse.size] += impulse
    metrics = analyze_signal_features(samples, samples, rate)["raw"]["broadband_impulses"]
    assert metrics["candidate_count"] > 24
    assert len(metrics["candidate_events"]) == 24
    assert metrics["candidate_truncated"] is True
    assert metrics["lpc_checked_events"] == 24
    assert metrics["candidate_rate_per_second"] > 0.0


def test_lpc_prediction_residual_is_high_for_an_injected_short_click() -> None:
    rate = 48_000
    time = np.arange(4 * rate, dtype=np.float64) / rate
    voice = (
        0.025 * np.sin(2 * np.pi * 115.0 * time)
        + 0.012 * np.sin(2 * np.pi * 230.0 * time)
        + 0.006 * np.sin(2 * np.pi * 345.0 * time)
    )
    samples = voice.copy()
    rng = np.random.default_rng(101)
    click = 0.2 * np.exp(-np.arange(48) / 8.0) * rng.normal(size=48)
    start = 2 * rate + rate // 2
    samples[start : start + click.size] += click
    features = analyze_signal_features(samples, samples, rate)
    events = features["raw"]["broadband_impulses"]["candidate_events"]
    near_click = [item for item in events if abs(item["start_seconds"] - 2.5) < 0.03]
    assert near_click
    event = max(near_click, key=lambda item: item["lpc_prediction_error_ratio"] or 0.0)
    assert event["lpc_prediction_error_ratio"] is not None
    assert event["lpc_prediction_error_ratio"] >= 4.0
    assert event["lpc_prediction_peak_ratio"] >= 8.0
    assert features["automatic_action"] == "none"


def test_lpc_prediction_residual_is_not_evaluated_without_prior_context() -> None:
    from audio_health_signal import _lpc_prediction_residual

    samples = np.sin(2 * np.pi * 100 * np.arange(100) / 48_000)
    result = _lpc_prediction_residual(samples, 10, 30, 48_000)
    assert result["prediction_error_ratio"] is None
    assert "prediction_error_ratio" in result
    assert result["prediction_error_ratio"] is None
