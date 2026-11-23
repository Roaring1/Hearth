"""Bounded, non-destructive signal features for short microphone QA samples.

The module reports measurements only. It does not classify a physical cause,
alter routing, or authorize automatic recovery.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

import numpy as np

_EPS = 1e-30
_TONE_MIN_HZ = 20.0
_TONE_MAX_HZ = 1000.0
_TONE_MIN_PROMINENCE_DB = 15.0
_TONE_FRAME_PERSISTENCE = 0.80
_TONE_MAX_LINES = 8
_BANDS = (
    ("20_80_hz", 20.0, 80.0),
    ("80_250_hz", 80.0, 250.0),
    ("250_500_hz", 250.0, 500.0),
    ("500_2k_hz", 500.0, 2000.0),
    ("2k_5k_hz", 2000.0, 5000.0),
    ("5k_12k_hz", 5000.0, 12000.0),
    ("12k_20k_hz", 12000.0, 20000.0),
)


def _db_power(power: float) -> float:
    return round(10.0 * math.log10(max(float(power), _EPS)), 2)


def _band_power(psd: np.ndarray, frequencies: np.ndarray, low: float, high: float) -> float:
    selected = (frequencies >= low) & (frequencies < min(high, float(frequencies[-1])))
    if not np.any(selected):
        return 0.0
    spacing = float(frequencies[1] - frequencies[0]) if frequencies.size > 1 else 0.0
    return float(np.sum(psd[selected]) * spacing)


def _local_floor_db(power: np.ndarray, frequencies: np.ndarray, target_hz: float) -> float:
    distance = np.abs(frequencies - target_hz)
    local = (distance >= 2.5) & (distance <= 35.0)
    local &= (frequencies >= _TONE_MIN_HZ) & (frequencies <= _TONE_MAX_HZ)
    if np.count_nonzero(local) < 8:
        return _db_power(float(np.median(power[local]))) if np.any(local) else _db_power(0.0)
    return _db_power(float(np.median(power[local])))


def _spectral_frames(
    samples: np.ndarray,
    sample_rate: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    frame_size = 2 * sample_rate
    hop = frame_size
    if samples.size < frame_size:
        return np.empty((0, 0)), np.empty(0), np.empty(0)

    nfft = 1 << (frame_size - 1).bit_length()
    window = np.hanning(frame_size).astype(np.float64)
    scale = sample_rate * float(np.sum(window * window))
    frames: list[np.ndarray] = []
    frame_levels: list[float] = []
    for start in range(0, samples.size - frame_size + 1, hop):
        segment = samples[start : start + frame_size]
        spectrum = np.fft.rfft(segment * window, n=nfft)
        power = (np.abs(spectrum) ** 2) / max(scale, _EPS)
        if power.size > 2:
            power[1:-1] *= 2.0
        frames.append(power)
        frame_levels.append(float(np.sqrt(np.mean(segment * segment))))

    if len(frames) < 2:
        return np.empty((0, 0)), np.empty(0), np.empty(0)

    # Retain all frames for frequency stability validation. Candidate peaks
    # are selected from lower-energy windows later in _persistent_tones.
    powers = np.vstack(frames)
    frequencies = np.fft.rfftfreq(nfft, 1.0 / sample_rate)
    return powers, frequencies, np.asarray(frame_levels, dtype=np.float64)


def _interpolated_peak_frequency(
    power: np.ndarray,
    frequencies: np.ndarray,
    peak_index: int,
) -> float:
    """Sub-bin parabolic peak estimate in log-power space."""
    if peak_index <= 0 or peak_index >= power.size - 1:
        return float(frequencies[peak_index])
    y0, y1, y2 = np.log(power[peak_index - 1 : peak_index + 2] + _EPS)
    denominator = y0 - 2.0 * y1 + y2
    if abs(float(denominator)) < 1e-15:
        return float(frequencies[peak_index])
    offset = 0.5 * float(y0 - y2) / float(denominator)
    offset = float(np.clip(offset, -0.5, 0.5))
    spacing = float(frequencies[1] - frequencies[0])
    return float(frequencies[peak_index] + offset * spacing)


def _persistent_tones(samples: np.ndarray, sample_rate: int) -> list[dict[str, Any]]:
    powers, frequencies, frame_levels = _spectral_frames(samples, sample_rate)
    if powers.size == 0:
        return []

    keep_count = max(2, math.ceil(powers.shape[0] * 0.25))
    selected = np.argsort(frame_levels)[:keep_count]
    candidate_powers = powers[selected]
    median_power = np.median(candidate_powers, axis=0)
    search = (frequencies >= _TONE_MIN_HZ) & (frequencies <= _TONE_MAX_HZ)
    indices = np.flatnonzero(search)
    if indices.size < 3:
        return []

    peaks = indices[1:-1][
        (median_power[indices[1:-1]] > median_power[indices[:-2]])
        & (median_power[indices[1:-1]] >= median_power[indices[2:]])
    ]
    candidates: list[dict[str, Any]] = []
    bin_width = float(frequencies[1] - frequencies[0])
    for peak_index in peaks:
        frequency = _interpolated_peak_frequency(median_power, frequencies, int(peak_index))
        prominence = _db_power(float(median_power[peak_index])) - _local_floor_db(
            median_power, frequencies, frequency
        )
        if prominence < _TONE_MIN_PROMINENCE_DB:
            continue

        present = 0
        line_powers: list[float] = []
        frame_frequencies: list[float] = []
        for frame_power in powers:
            around = np.flatnonzero(np.abs(frequencies - frequency) <= 2.0)
            if around.size == 0:
                continue
            frame_peak = int(around[int(np.argmax(frame_power[around]))])
            frame_frequency = _interpolated_peak_frequency(frame_power, frequencies, frame_peak)
            frame_prominence = _db_power(float(frame_power[frame_peak])) - _local_floor_db(
                frame_power, frequencies, frame_frequency
            )
            if frame_prominence >= _TONE_MIN_PROMINENCE_DB:
                present += 1
                frame_frequencies.append(frame_frequency)
                line_mask = np.abs(frequencies - frame_frequency) <= max(1.5, 2 * bin_width)
                line_powers.append(float(np.sum(frame_power[line_mask]) * bin_width))

        persistence = present / max(powers.shape[0], 1)
        frequency_spread = (
            float(np.std(np.asarray(frame_frequencies))) if frame_frequencies else float("inf")
        )
        minimum_present = max(3, math.ceil(powers.shape[0] * _TONE_FRAME_PERSISTENCE))
        if present < minimum_present or frequency_spread > 0.40:
            continue
        candidates.append(
            {
                "frequency_hz": round(frequency, 2),
                "prominence_db": round(prominence, 2),
                "persistence": round(persistence, 3),
                "frequency_std_hz": round(frequency_spread, 3),
                "frames_detected": present,
                "frames_total": int(powers.shape[0]),
                "candidate_frames": int(keep_count),
                "selection": "lower_energy_quartile_min_2_nonoverlapping_2s_windows",
                "line_power_dbfs": (
                    _db_power(float(np.median(line_powers))) if line_powers else None
                ),
            }
        )

    candidates.sort(
        key=lambda item: (item["persistence"], item["prominence_db"]),
        reverse=True,
    )
    # Peaks closer than the analysis resolution are one unresolved line, not
    # independent tones. Keep only the strongest representative of each group.
    distinct: list[dict[str, Any]] = []
    for item in candidates:
        frequency = float(item["frequency_hz"])
        if any(abs(frequency - float(existing["frequency_hz"])) <= 2.5 for existing in distinct):
            continue
        distinct.append(item)
        if len(distinct) >= _TONE_MAX_LINES:
            break
    return distinct


def _low_energy_metrics(samples: np.ndarray, sample_rate: int) -> dict[str, Any]:
    frame_size = sample_rate // 10
    frame_count = samples.size // frame_size
    if frame_count < 3:
        return {
            "frames": frame_count,
            "rms_dbfs": None,
            "5k_12k_dbfs": None,
            "5k_12k_flatness": None,
        }

    frames = samples[: frame_count * frame_size].reshape(frame_count, frame_size)
    frame_rms = np.sqrt(np.mean(frames * frames, axis=1))
    low_cut = float(np.percentile(frame_rms, 25.0))
    selected = frames[frame_rms <= low_cut]
    if selected.shape[0] < 3:
        selected = frames[np.argsort(frame_rms)[: min(3, frame_count)]]

    nfft = 1 << (frame_size - 1).bit_length()
    window = np.hanning(frame_size).astype(np.float64)
    scale = sample_rate * float(np.sum(window * window))
    spectra = np.fft.rfft(selected * window[None, :], n=nfft, axis=1)
    powers = (np.abs(spectra) ** 2) / max(scale, _EPS)
    if powers.shape[1] > 2:
        powers[:, 1:-1] *= 2.0
    mean_power = np.mean(powers, axis=0)
    frequencies = np.fft.rfftfreq(nfft, 1.0 / sample_rate)
    high_mask = (frequencies >= 5000.0) & (frequencies < min(12000.0, sample_rate / 2))
    high_values = mean_power[high_mask]
    if high_values.size:
        flatness = float(
            np.exp(np.mean(np.log(high_values + _EPS))) / (np.mean(high_values) + _EPS)
        )
        high_power = _band_power(mean_power, frequencies, 5000.0, 12000.0)
    else:
        flatness = None
        high_power = None

    return {
        "frames": int(selected.shape[0]),
        "rms_dbfs": _db_power(float(np.median(frame_rms[frame_rms <= low_cut]))),
        "5k_12k_dbfs": _db_power(high_power) if high_power is not None else None,
        "5k_12k_flatness": round(flatness, 4) if flatness is not None else None,
    }


def _highpass_transient_metrics(samples: np.ndarray, sample_rate: int) -> dict[str, Any]:
    """Describe 5kHz+ impulsiveness without treating speech peaks as faults."""
    if samples.size < sample_rate // 2:
        return {
            "hf_rms_dbfs": None,
            "hf_peak_dbfs": None,
            "hf_crest_db": None,
            "hf_excess_kurtosis": None,
            "transient_frames": 0,
            "transient_rate_per_second": None,
            "transient_threshold_dbfs": None,
            "sample_peak_dbfs": None,
            "near_clip_fraction": None,
        }

    taps = 31
    midpoint = (taps - 1) / 2
    normalized = min(5000.0 / sample_rate, 0.45)
    lowpass = []
    for index in range(taps):
        offset = index - midpoint
        if offset == 0:
            value = 2.0 * normalized
        else:
            value = math.sin(2.0 * math.pi * normalized * offset) / (math.pi * offset)
        window = 0.54 - 0.46 * math.cos(2.0 * math.pi * index / (taps - 1))
        lowpass.append(value * window)
    lowpass_array = np.asarray(lowpass, dtype=np.float64)
    lowpass_array /= max(float(np.sum(lowpass_array)), _EPS)
    highpass = -lowpass_array
    highpass[taps // 2] += 1.0
    filtered = np.convolve(samples, highpass, mode="same")

    rms = float(np.sqrt(np.mean(filtered * filtered)))
    peak = float(np.max(np.abs(filtered)))
    crest_db = 20.0 * math.log10(max(peak, _EPS) / max(rms, _EPS))
    centered = filtered - float(np.mean(filtered))
    second = float(np.mean(centered * centered))
    kurtosis = float(np.mean(centered**4) / (second * second) - 3.0) if second > _EPS else 0.0

    frame_size = max(sample_rate // 100, 1)
    frame_count = samples.size // frame_size
    frames = filtered[: frame_count * frame_size].reshape(frame_count, frame_size)
    frame_rms = np.sqrt(np.mean(frames * frames, axis=1))
    median = float(np.median(frame_rms))
    mad = float(np.median(np.abs(frame_rms - median)))
    threshold = max(median + 8.0 * 1.4826 * mad, median * 4.0, 1e-7)
    above = frame_rms > threshold
    transient_count = 0
    in_event = False
    for event in above:
        if event and not in_event:
            transient_count += 1
        in_event = bool(event)

    sample_peak = float(np.max(np.abs(samples)))
    return {
        "hf_rms_dbfs": _db_power(rms * rms),
        "hf_peak_dbfs": round(20.0 * math.log10(max(peak, _EPS)), 2),
        "hf_crest_db": round(crest_db, 2),
        "hf_excess_kurtosis": round(kurtosis, 2),
        "transient_frames": int(transient_count),
        "transient_rate_per_second": round(transient_count / (samples.size / sample_rate), 3),
        "transient_threshold_dbfs": round(20.0 * math.log10(max(threshold, _EPS)), 2),
        "sample_peak_dbfs": round(20.0 * math.log10(max(sample_peak, _EPS)), 2),
        "near_clip_fraction": round(float(np.mean(np.abs(samples) >= 0.98)), 8),
    }


def _run_summary(mask: np.ndarray, minimum_length: int) -> tuple[int, int]:
    """Return (qualifying run count, maximum run length) for a boolean mask."""
    if mask.size == 0:
        return 0, 0
    starts = np.flatnonzero(mask & ~np.concatenate(([False], mask[:-1])))
    ends = np.flatnonzero(mask & ~np.concatenate((mask[1:], [False]))) + 1
    lengths = ends - starts
    selected = lengths[lengths >= minimum_length]
    return int(selected.size), int(np.max(lengths)) if lengths.size else 0


def _integrity_metrics(
    samples: Sequence[float] | np.ndarray,
    sample_rate: int,
) -> dict[str, Any]:
    """Report raw sample integrity facts without interpreting silence as a fault."""
    values = np.asarray(samples, dtype=np.float64).reshape(-1)
    finite = np.isfinite(values)
    nonfinite_count = int(values.size - np.count_nonzero(finite))
    if not np.any(finite):
        return {
            "sample_count": int(values.size),
            "nonfinite_samples": nonfinite_count,
            "exact_zero_runs_ge_1ms": 0,
            "max_exact_zero_run_ms": None,
            "max_repeated_nonzero_run_ms": None,
            "full_scale_samples": 0,
            "full_scale_runs_ge_2_samples": 0,
            "max_full_scale_run_ms": None,
        }

    minimum_zero = max(1, round(sample_rate * 0.001))
    zero_runs, max_zero = _run_summary(finite & (np.abs(values) <= 1e-12), minimum_zero)
    if values.size > 1:
        equal_adjacent = (
            finite[1:] & finite[:-1] & (values[1:] == values[:-1]) & (np.abs(values[1:]) > 1e-12)
        )
        _, max_equal_adjacencies = _run_summary(equal_adjacent, minimum_zero - 1)
        max_repeated = max_equal_adjacencies + 1 if max_equal_adjacencies else 0
    else:
        max_repeated = 1 if values.size == 1 and finite[0] and abs(float(values[0])) > 1e-12 else 0

    full_scale = finite & (np.abs(values) >= 0.99999)
    full_scale_samples = int(np.count_nonzero(full_scale))
    full_scale_runs, max_full_scale = _run_summary(full_scale, 2)
    return {
        "sample_count": int(values.size),
        "nonfinite_samples": nonfinite_count,
        "exact_zero_runs_ge_1ms": zero_runs,
        "max_exact_zero_run_ms": round(max_zero * 1000.0 / sample_rate, 3) if max_zero else 0.0,
        "max_repeated_nonzero_run_ms": round(max_repeated * 1000.0 / sample_rate, 3)
        if max_repeated
        else 0.0,
        "full_scale_samples": full_scale_samples,
        "full_scale_runs_ge_2_samples": full_scale_runs,
        "max_full_scale_run_ms": round(max_full_scale * 1000.0 / sample_rate, 3)
        if max_full_scale
        else 0.0,
    }


def _channel_summary(samples: Sequence[float] | np.ndarray, sample_rate: int) -> dict[str, Any]:
    values = np.asarray(samples, dtype=np.float64).reshape(-1)
    values = np.nan_to_num(values, copy=True, nan=0.0, posinf=0.0, neginf=0.0)
    if values.size == 0 or sample_rate < 8000:
        return {
            "sample_count": int(values.size),
            "duration_seconds": 0.0,
            "band_rms_dbfs": {},
            "high_frequency_share": None,
            "spectral_flatness_2k_12k": None,
            "low_energy": _low_energy_metrics(values, sample_rate),
            "transients": _highpass_transient_metrics(values, sample_rate),
            "persistent_tones": [],
        }

    duration = values.size / sample_rate
    # One-second PSD windows, 50% overlap. Each returned metric is an estimate
    # over this bounded sample, not a calibrated SPL/noise measurement.
    frame_size = min(sample_rate * 2, values.size)
    hop = sample_rate
    nfft = 1 << (frame_size - 1).bit_length()
    window = np.hanning(frame_size).astype(np.float64)
    scale = sample_rate * float(np.sum(window * window))
    spectra = []
    for start in range(0, values.size - frame_size + 1, hop):
        segment = values[start : start + frame_size]
        spectrum = np.fft.rfft(segment * window, n=nfft)
        power = (np.abs(spectrum) ** 2) / max(scale, _EPS)
        if power.size > 2:
            power[1:-1] *= 2.0
        spectra.append(power)
    frequencies = np.fft.rfftfreq(nfft, 1.0 / sample_rate)
    average_power = np.mean(np.vstack(spectra), axis=0) if spectra else np.zeros_like(frequencies)
    total_power = _band_power(average_power, frequencies, 20.0, min(20000.0, sample_rate / 2))
    high_power = _band_power(average_power, frequencies, 5000.0, min(20000.0, sample_rate / 2))
    band_levels = {
        name: _db_power(_band_power(average_power, frequencies, low, high))
        for name, low, high in _BANDS
    }
    flat_mask = (frequencies >= 2000.0) & (frequencies < min(12000.0, sample_rate / 2))
    flat_values = average_power[flat_mask]
    flatness = (
        float(np.exp(np.mean(np.log(flat_values + _EPS))) / (np.mean(flat_values) + _EPS))
        if flat_values.size
        else None
    )
    return {
        "sample_count": int(values.size),
        "duration_seconds": round(duration, 3),
        "band_rms_dbfs": band_levels,
        "high_frequency_share": round(high_power / max(total_power, _EPS), 6),
        "spectral_flatness_2k_12k": round(flatness, 4) if flatness is not None else None,
        "low_energy": _low_energy_metrics(values, sample_rate),
        "transients": _highpass_transient_metrics(values, sample_rate),
        "persistent_tones": _persistent_tones(values, sample_rate),
    }


def _match_tones(raw: list[dict[str, Any]], processed: list[dict[str, Any]]) -> str:
    raw_frequencies = [float(item["frequency_hz"]) for item in raw]
    post_frequencies = [float(item["frequency_hz"]) for item in processed]
    if raw_frequencies and post_frequencies:
        if any(abs(left - right) <= 2.0 for left in raw_frequencies for right in post_frequencies):
            return "present_raw_and_processed"
        return "different_persistent_lines"
    if raw_frequencies:
        return "present_raw_only"
    if post_frequencies:
        return "present_processed_only"
    return "no_persistent_line_detected"


def analyze_signal_features(
    raw_samples: Sequence[float] | np.ndarray,
    processed_samples: Sequence[float] | np.ndarray,
    sample_rate: int = 48_000,
) -> dict[str, Any]:
    """Return compact numeric features and raw/post line localization.

    The 100 ms low-energy estimate is the lower quartile of frame levels. It is
    deliberately not called a calibrated noise floor or speech-free interval.
    Persistent spectral lines are candidates, not a ground-loop/mains diagnosis.
    """
    raw_integrity = _integrity_metrics(raw_samples, sample_rate)
    processed_integrity = _integrity_metrics(processed_samples, sample_rate)
    raw = _channel_summary(raw_samples, sample_rate)
    processed = _channel_summary(processed_samples, sample_rate)
    raw_transients = raw["transients"]
    processed_transients = processed["transients"]

    def delta(field: str) -> float | None:
        before = raw_transients.get(field)
        after = processed_transients.get(field)
        if not isinstance(before, (int, float)) or not isinstance(after, (int, float)):
            return None
        return round(float(after - before), 3)

    return {
        "version": 1,
        "sample_rate": int(sample_rate),
        "raw": raw,
        "processed": processed,
        "integrity": {"raw": raw_integrity, "processed": processed_integrity},
        "tone_localization": _match_tones(raw["persistent_tones"], processed["persistent_tones"]),
        "raw_to_processed": {
            "band_level_delta_db": {
                name: round(processed["band_rms_dbfs"][name] - raw["band_rms_dbfs"][name], 2)
                for name in raw["band_rms_dbfs"]
            },
            "high_frequency_share_delta": round(
                processed["high_frequency_share"] - raw["high_frequency_share"], 6
            ),
            "hf_rms_delta_db": delta("hf_rms_dbfs"),
            "hf_peak_delta_db": delta("hf_peak_dbfs"),
            "hf_crest_delta_db": delta("hf_crest_db"),
            "hf_excess_kurtosis_delta": delta("hf_excess_kurtosis"),
            "transient_frame_delta": delta("transient_frames"),
            "near_clip_fraction_delta": delta("near_clip_fraction"),
        },
        "automatic_action": "none",
        "interpretation": "measurement_only; requires profile baseline and source review",
    }
