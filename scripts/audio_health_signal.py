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

# Four 12-tap phase filters from ITU-R BS.1770-5 Annex 2, in phase order.
# The standard's optional 12.04 dB attenuation is omitted because arithmetic
# is floating-point. The sample peak is also included in the max-hold.
_BS1770_ANNEX2_PHASES = np.asarray(
    [
        [
            0.0017089843750,
            0.0109863281250,
            -0.0196533203125,
            0.0332031250000,
            -0.0594482421875,
            0.1373291015625,
            0.9721679687500,
            -0.1022949218750,
            0.0476074218750,
            -0.0266113281250,
            0.0148925781250,
            -0.0083007812500,
        ],
        [
            -0.0291748046875,
            0.0292968750000,
            -0.0517578125000,
            0.0891113281250,
            -0.1665039062500,
            0.4650878906250,
            0.7797851562500,
            -0.2003173828125,
            0.1015625000000,
            -0.0582275390625,
            0.0330810546875,
            -0.0189208984375,
        ],
        [
            -0.0189208984375,
            0.0330810546875,
            -0.0582275390625,
            0.1015625000000,
            -0.2003173828125,
            0.7797851562500,
            0.4650878906250,
            -0.1665039062500,
            0.0891113281250,
            -0.0517578125000,
            0.0292968750000,
            -0.0291748046875,
        ],
        [
            -0.0083007812500,
            0.0148925781250,
            -0.0266113281250,
            0.0476074218750,
            -0.1022949218750,
            0.9721679687500,
            0.1373291015625,
            -0.0594482421875,
            0.0332031250000,
            -0.0196533203125,
            0.0109863281250,
            0.0017089843750,
        ],
    ],
    dtype=np.float64,
)
_TONE_MIN_HZ = 20.0
_TONE_MAX_HZ = 1000.0
_TONE_MIN_PROMINENCE_DB = 15.0
_TONE_FRAME_PERSISTENCE = 0.80
_TONE_MAX_LINES = 8
# Exploratory heuristics for 8-second samples; never authorize automatic repair.
_IMPULSE_MIN_HF_RMS_DBFS = -55.0
_IMPULSE_MIN_HF_PEAK_DBFS = -35.0
_IMPULSE_MIN_CREST_DB = 27.0
_IMPULSE_MIN_EXCESS_KURTOSIS = 80.0
_IMPULSE_MIN_HF_SHARE = 0.005
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


def _true_peak_metrics(
    samples: Sequence[float] | np.ndarray,
    sample_rate: int,
    *,
    oversample_factor: int = 4,
    block_frames: int = 8192,
) -> dict[str, Any]:
    """Estimate true peak using the Annex 2 48-tap, four-phase FIR."""
    values = np.asarray(samples, dtype=np.float64).reshape(-1)
    finite = np.isfinite(values)
    values = np.where(finite, values, 0.0)
    if values.size == 0 or sample_rate < 8000:
        return {
            "sample_peak_dbfs": None,
            "true_peak_dbtp": None,
            "intersample_increase_db": None,
            "oversample_factor": oversample_factor,
            "over_full_scale": None,
            "nonfinite_samples_excluded": int(values.size - np.count_nonzero(finite)),
            "supported_sample_rate_hz": 48000,
            "unsupported_reason": "empty sample or sample rate below feature minimum",
        }
    if sample_rate != 48000:
        sample_peak = float(np.max(np.abs(values)))
        sample_dbfs = 20.0 * math.log10(max(sample_peak, _EPS))
        return {
            "sample_peak_dbfs": round(sample_dbfs, 3),
            "true_peak_dbtp": None,
            "intersample_increase_db": None,
            "oversample_factor": oversample_factor,
            "over_full_scale": None,
            "nonfinite_samples_excluded": int(values.size - np.count_nonzero(finite)),
            "supported_sample_rate_hz": 48000,
            "unsupported_reason": "Annex 2 coefficient set is only implemented for 48 kHz",
        }
    if oversample_factor != 4:
        raise ValueError("true-peak measurement currently requires 4x oversampling")
    if block_frames < 256:
        raise ValueError("true-peak processing block size is too small")

    sample_peak = float(np.max(np.abs(values)))
    # The 48-tap linear-phase filter uses 12 original samples per phase.
    # Six samples of edge extension allow every complete 12-sample window to
    # be evaluated. Interpolated results at reflected boundaries are excluded;
    # exact stored sample peaks remain included across the full input.
    padded = np.pad(values, (6, 6), mode="reflect")
    windows = np.lib.stride_tricks.sliding_window_view(padded, 12)
    first_window = 6
    stop_window = max(first_window, values.size - 5)
    true_peak = sample_peak
    for start in range(first_window, stop_window, block_frames):
        end = min(stop_window, start + block_frames)
        block = windows[start:end]
        for phase_coefficients in _BS1770_ANNEX2_PHASES:
            reconstructed = block @ phase_coefficients
            if reconstructed.size:
                true_peak = max(true_peak, float(np.max(np.abs(reconstructed))))

    sample_dbfs = 20.0 * math.log10(max(sample_peak, _EPS))
    true_peak_dbtp = 20.0 * math.log10(max(true_peak, _EPS))
    return {
        "sample_peak_dbfs": round(sample_dbfs, 3),
        "true_peak_dbtp": round(true_peak_dbtp, 3),
        "intersample_increase_db": round(true_peak_dbtp - sample_dbfs, 3),
        "oversample_factor": oversample_factor,
        "over_full_scale": bool(true_peak >= 1.0),
        "nonfinite_samples_excluded": int(values.size - np.count_nonzero(finite)),
        "method": "ITU-R_BS.1770-5_Annex2_48-tap_4-phase_FIR",
        "supported_sample_rate_hz": 48000,
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


def _lpc_prediction_residual(
    samples: np.ndarray,
    start_sample: int,
    end_sample: int,
    sample_rate: int,
    *,
    order: int = 12,
    context_ms: float = 25.0,
) -> dict[str, float | bool | None]:
    """Measure how unpredictable a short event is from its preceding context."""
    context_length = max(order * 4, round(sample_rate * context_ms / 1000.0))
    context_start = max(0, start_sample - context_length)
    if start_sample - context_start < order * 4 or end_sample <= start_sample:
        return {
            "prediction_error_ratio": None,
            "prediction_peak_ratio": None,
        }

    context = np.asarray(samples[context_start:start_sample], dtype=np.float64)
    event = np.asarray(samples[start_sample:end_sample], dtype=np.float64)
    if context.size < order * 4 or event.size == 0:
        return {
            "prediction_error_ratio": None,
            "prediction_peak_ratio": None,
        }

    mean = float(np.mean(context))
    context = context - mean
    event = event - mean
    count = context.size
    autocorrelation = np.asarray(
        [float(np.dot(context[lag:], context[: count - lag]) / count) for lag in range(order + 1)],
        dtype=np.float64,
    )
    if not np.isfinite(autocorrelation).all() or autocorrelation[0] <= 1e-18:
        return {
            "prediction_error_ratio": None,
            "prediction_peak_ratio": None,
        }

    indices = np.abs(np.subtract.outer(np.arange(order), np.arange(order)))
    covariance = autocorrelation[indices]
    ridge = max(float(autocorrelation[0]) * 1e-4, 1e-18)
    covariance = covariance + np.eye(order, dtype=np.float64) * ridge
    try:
        coefficients = np.linalg.solve(covariance, autocorrelation[1 : order + 1])
    except np.linalg.LinAlgError:
        return {
            "prediction_error_ratio": None,
            "prediction_peak_ratio": None,
        }

    def prediction_errors(values: np.ndarray) -> np.ndarray:
        if values.size <= order:
            return np.empty(0, dtype=np.float64)
        windows = np.lib.stride_tricks.sliding_window_view(values, order + 1)
        predicted = windows[:, :order][:, ::-1] @ coefficients
        return windows[:, order] - predicted

    baseline_errors = prediction_errors(context)
    event_history = np.concatenate((context[-order:], event))
    event_errors = prediction_errors(event_history)
    if baseline_errors.size == 0 or event_errors.size == 0:
        return {
            "prediction_error_ratio": None,
            "prediction_peak_ratio": None,
        }

    baseline_rms = float(np.sqrt(np.mean(baseline_errors * baseline_errors)))
    event_rms = float(np.sqrt(np.mean(event_errors * event_errors)))
    event_peak = float(np.max(np.abs(event_errors)))
    floor = max(float(np.sqrt(autocorrelation[0])) * 1e-4, 1e-9)
    denominator = max(baseline_rms, floor)
    rms_ratio = event_rms / denominator
    peak_ratio = event_peak / denominator
    return {
        "prediction_error_ratio": round(rms_ratio, 3),
        "prediction_peak_ratio": round(peak_ratio, 3),
    }


def _broadband_impulse_metrics(
    samples: np.ndarray,
    sample_rate: int,
) -> dict[str, Any]:
    """Count short multi-band bursts against a local robust baseline.

    This is a diagnostic candidate feature, not a perceptual click classifier.
    """
    frame_size = max(1, round(sample_rate * 0.005))
    hop = max(1, frame_size // 2)
    if samples.size < frame_size * 4:
        return {
            "frame_ms": round(1000.0 * frame_size / sample_rate, 3),
            "hop_ms": round(1000.0 * hop / sample_rate, 3),
            "candidate_count": 0,
            "candidate_events": [],
            "analysis_frames": 0,
        }

    frame_count = 1 + (samples.size - frame_size) // hop
    frame_view = np.lib.stride_tricks.sliding_window_view(samples, frame_size)[::hop]
    nfft = 1 << (frame_size - 1).bit_length()
    window = np.hanning(frame_size).astype(np.float64)
    frequencies = np.fft.rfftfreq(nfft, 1.0 / sample_rate)

    bands = ((2500.0, 5000.0), (5000.0, 10000.0), (10000.0, 20000.0))
    masks = [
        (frequencies >= low) & (frequencies < min(high, sample_rate / 2)) for low, high in bands
    ]
    # Work in bounded batches. A whole-sample frames x FFT matrix needlessly
    # multiplies temporary memory under the healthd service's 96 MiB limit.
    band_powers = np.empty((frame_count, len(bands)), dtype=np.float64)
    fft_batch_frames = 256
    for batch_start in range(0, frame_count, fft_batch_frames):
        batch_end = min(frame_count, batch_start + fft_batch_frames)
        frames = frame_view[batch_start:batch_end]
        spectra = np.fft.rfft(frames * window[None, :], n=nfft, axis=1)
        power = np.abs(spectra) ** 2
        for band_index, mask in enumerate(masks):
            band_powers[batch_start:batch_end, band_index] = np.sum(
                power[:, mask],
                axis=1,
            )
        del spectra, power
    if np.any(~np.isfinite(band_powers)):
        return {
            "frame_ms": round(1000.0 * frame_size / sample_rate, 3),
            "hop_ms": round(1000.0 * hop / sample_rate, 3),
            "candidate_count": 0,
            "candidate_events": [],
            "analysis_frames": int(frame_count),
        }

    # Exclude the center +/-10ms so the event cannot inflate its own baseline.
    radius = max(4, round(0.100 * sample_rate / hop))
    exclusion = max(1, round(0.010 * sample_rate / hop))
    local_medians = np.zeros_like(band_powers)
    local_mads = np.zeros_like(band_powers)
    for index in range(frame_count):
        left = max(0, index - radius)
        right = min(frame_count, index + radius + 1)
        context = np.concatenate(
            (
                band_powers[left : max(left, index - exclusion)],
                band_powers[min(right, index + exclusion + 1) : right],
            ),
            axis=0,
        )
        if context.shape[0] < 4:
            context = band_powers[left:right]
        median = np.median(context, axis=0) if context.size else np.zeros(3)
        mad = np.median(np.abs(context - median), axis=0) if context.size else np.zeros(3)
        local_medians[index] = median
        local_mads[index] = mad

    thresholds = np.maximum.reduce(
        (
            local_medians * 6.0,
            local_medians + 10.0 * 1.4826 * local_mads,
            np.full_like(local_medians, 1e-9),
        )
    )
    elevated = band_powers > thresholds
    active = np.count_nonzero(elevated, axis=1) >= 2
    # Fill one-frame gaps caused by overlapping windows, not wider gaps.
    if active.size >= 3:
        active[1:-1] |= active[:-2] & active[2:]

    event_starts = np.flatnonzero(active & ~np.concatenate(([False], active[:-1])))
    event_ends = np.flatnonzero(active & ~np.concatenate((active[1:], [False]))) + 1
    events: list[dict[str, Any]] = []
    for first, end in zip(event_starts, event_ends, strict=True):
        frame_count_event = int(end - first)
        duration_ms = (
            frame_count_event - 1
        ) * hop * 1000.0 / sample_rate + frame_size * 1000.0 / sample_rate
        # Long broadband spans are more consistent with fricatives/noise than clicks.
        if duration_ms > 15.0:
            continue
        ratios = band_powers[first:end] / np.maximum(thresholds[first:end], 1e-12)
        start_seconds = float(first * hop / sample_rate)
        events.append(
            {
                "start_seconds": round(start_seconds, 4),
                "duration_ms": round(duration_ms, 3),
                "max_band_threshold_ratio": round(float(np.max(ratios)), 2),
                "bands_elevated": int(np.max(np.count_nonzero(elevated[first:end], axis=1))),
                "lpc_prediction_error_ratio": None,
                "lpc_prediction_peak_ratio": None,
            }
        )

    # The LPC stage is more expensive than the band-energy candidate pass.
    # Evaluate only the 24 strongest events and retain their timestamps.
    strongest_indices = sorted(
        range(len(events)),
        key=lambda index: float(events[index]["max_band_threshold_ratio"]),
        reverse=True,
    )[:24]
    for index in strongest_indices:
        event = events[index]
        start_sample = round(float(event["start_seconds"]) * sample_rate)
        event_samples = round(float(event["duration_ms"]) * sample_rate / 1000.0)
        prediction = _lpc_prediction_residual(
            samples,
            start_sample,
            min(samples.size, start_sample + event_samples),
            sample_rate,
        )
        event["lpc_prediction_error_ratio"] = prediction["prediction_error_ratio"]
        event["lpc_prediction_peak_ratio"] = prediction["prediction_peak_ratio"]

    evaluated = [events[index] for index in strongest_indices]
    evaluated.sort(key=lambda item: float(item["start_seconds"]))
    return {
        "frame_ms": round(1000.0 * frame_size / sample_rate, 3),
        "hop_ms": round(1000.0 * hop / sample_rate, 3),
        "candidate_count": len(events),
        "candidate_rate_per_second": round(len(events) / (samples.size / sample_rate), 3),
        "candidate_events": evaluated,
        "analysis_frames": int(frame_count),
        "candidate_truncated": len(events) > 24,
        "lpc_checked_events": len(evaluated),
        "threshold_description": (
            "local median/MAD; >=2 of 3 high-frequency bands; <=15ms; "
            "LPC prediction residual ratios are descriptive only"
        ),
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
            "true_peak": _true_peak_metrics(values, sample_rate),
            "broadband_impulses": _broadband_impulse_metrics(values, sample_rate),
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
        "true_peak": _true_peak_metrics(values, sample_rate),
        "broadband_impulses": _broadband_impulse_metrics(values, sample_rate),
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


def _impulse_candidate(
    transients: dict[str, Any],
    high_frequency_share: float,
) -> dict[str, Any]:
    """A descriptive, multi-cue impulse candidate; never an action trigger."""
    values = {
        "hf_rms_dbfs": transients.get("hf_rms_dbfs"),
        "hf_peak_dbfs": transients.get("hf_peak_dbfs"),
        "hf_crest_db": transients.get("hf_crest_db"),
        "hf_excess_kurtosis": transients.get("hf_excess_kurtosis"),
    }
    valid = all(isinstance(value, (int, float)) for value in values.values())
    cues = {
        "high_frequency_activity": bool(
            valid and float(values["hf_rms_dbfs"]) >= _IMPULSE_MIN_HF_RMS_DBFS
        ),
        "high_frequency_peak": bool(
            valid and float(values["hf_peak_dbfs"]) >= _IMPULSE_MIN_HF_PEAK_DBFS
        ),
        "high_crest": bool(valid and float(values["hf_crest_db"]) >= _IMPULSE_MIN_CREST_DB),
        "high_excess_kurtosis": bool(
            valid and float(values["hf_excess_kurtosis"]) >= _IMPULSE_MIN_EXCESS_KURTOSIS
        ),
        "high_frequency_share": high_frequency_share >= _IMPULSE_MIN_HF_SHARE,
    }
    return {
        "candidate": all(cues.values()),
        "confidence": "heuristic_only",
        "cues": cues,
        "thresholds": {
            "hf_rms_dbfs_min": _IMPULSE_MIN_HF_RMS_DBFS,
            "hf_peak_dbfs_min": _IMPULSE_MIN_HF_PEAK_DBFS,
            "hf_crest_db_min": _IMPULSE_MIN_CREST_DB,
            "hf_excess_kurtosis_min": _IMPULSE_MIN_EXCESS_KURTOSIS,
            "high_frequency_share_min": _IMPULSE_MIN_HF_SHARE,
        },
        "automatic_action": "none",
    }


def _impulse_localization(raw_candidate: bool, processed_candidate: bool) -> str:
    if raw_candidate and processed_candidate:
        return "both_points_candidate"
    if processed_candidate:
        return "processed_only_candidate"
    if raw_candidate:
        return "raw_only_candidate"
    return "no_candidate"


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
    if sample_rate < 8000:
        raise ValueError("sample_rate must be at least 8000 Hz for microphone signal analysis")
    raw_integrity = _integrity_metrics(raw_samples, sample_rate)
    processed_integrity = _integrity_metrics(processed_samples, sample_rate)
    raw = _channel_summary(raw_samples, sample_rate)
    processed = _channel_summary(processed_samples, sample_rate)
    raw["impulsive_event_candidate"] = _impulse_candidate(
        raw["transients"], float(raw["high_frequency_share"] or 0.0)
    )
    processed["impulsive_event_candidate"] = _impulse_candidate(
        processed["transients"], float(processed["high_frequency_share"] or 0.0)
    )
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
        "impulse_localization": _impulse_localization(
            raw["impulsive_event_candidate"]["candidate"],
            processed["impulsive_event_candidate"]["candidate"],
        ),
        "raw_to_processed": {
            "band_level_delta_db": {
                name: round(processed["band_rms_dbfs"][name] - raw["band_rms_dbfs"][name], 2)
                for name in raw["band_rms_dbfs"]
            },
            "high_frequency_share_delta": round(
                processed["high_frequency_share"] - raw["high_frequency_share"], 6
            ),
            "true_peak_delta_dbtp": (
                round(
                    processed["true_peak"]["true_peak_dbtp"] - raw["true_peak"]["true_peak_dbtp"],
                    3,
                )
                if isinstance(raw["true_peak"].get("true_peak_dbtp"), (int, float))
                and isinstance(processed["true_peak"].get("true_peak_dbtp"), (int, float))
                else None
            ),
            "hf_rms_delta_db": delta("hf_rms_dbfs"),
            "hf_peak_delta_db": delta("hf_peak_dbfs"),
            "hf_crest_delta_db": delta("hf_crest_db"),
            "hf_excess_kurtosis_delta": delta("hf_excess_kurtosis"),
            "transient_frame_delta": delta("transient_frames"),
            "near_clip_fraction_delta": delta("near_clip_fraction"),
            "broadband_impulse_candidate_delta": (
                processed["broadband_impulses"]["candidate_count"]
                - raw["broadband_impulses"]["candidate_count"]
            ),
        },
        "automatic_action": "none",
        "interpretation": "measurement_only; requires profile baseline and source review",
    }
