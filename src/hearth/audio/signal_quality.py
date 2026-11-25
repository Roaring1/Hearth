"""Pure-Python, bounded, measurement-only signal-quality primitives.

The engine consumes normalized mono PCM samples. It never captures audio,
starts subprocesses, queries PipeWire, persists samples, or performs recovery.
Thresholds here produce *candidates*, not validated fault verdicts.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from itertools import pairwise
from math import atan2, cos, isfinite, log10, sin, sqrt
from statistics import median


class SignalState(StrEnum):
    HEALTHY_OBSERVED = "healthy_observed"
    SUSPECT = "suspect"
    NO_SAMPLE = "no_sample"
    INCONCLUSIVE = "inconclusive"


class TransportState(StrEnum):
    VERIFIED = "verified"
    UNKNOWN = "unknown"
    NODE_CHANGED = "node_changed"
    COUNTER_RESET = "counter_reset"


@dataclass(frozen=True, slots=True)
class StableToneMetrics:
    classification: str
    frequency_hz: float | None
    frequency_variation_percent: float | None
    confidence: str


@dataclass(frozen=True, slots=True)
class StereoMetrics:
    sample_count: int
    sample_rate: int
    correlation: float | None
    phase_difference_degrees: float | None
    phase_confidence: str
    left_true_peak: float | None
    right_true_peak: float | None
    peak_difference_db: float | None


@dataclass(frozen=True, slots=True)
class StereoReport:
    left: SignalReport
    right: SignalReport
    metrics: StereoMetrics | None
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SignalMetrics:
    sample_count: int
    sample_rate: int
    duration_seconds: float
    rms: float
    peak: float
    dc_offset: float
    crest_factor_db: float | None
    difference_rms: float
    difference_ratio: float
    clipped_sample_count: int
    clipped_runs: int
    longest_clip_run: int
    near_zero_sample_count: int
    longest_near_zero_run: int
    transient_candidates: int
    transient_candidate_indices: tuple[int, ...]
    true_peak_estimate: float
    stable_tone: StableToneMetrics


@dataclass(frozen=True, slots=True)
class SignalReport:
    state: SignalState
    metrics: SignalMetrics | None
    reasons: tuple[str, ...]
    limitations: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TapComparison:
    raw: SignalReport
    processed: SignalReport
    localization: str
    rms_ratio: float | None
    confidence: str
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TransportSnapshot:
    node_id: str
    err_count: int
    sample_rate: int | None = None
    quantum: int | None = None
    role: str = "node"


@dataclass(frozen=True, slots=True)
class TransportDelta:
    state: TransportState
    err_delta: int | None
    sample_rate_changed: bool | None
    quantum_changed: bool | None
    reason: str


class DropoutState(StrEnum):
    CANDIDATE = "candidate"
    NO_CANDIDATE = "no_candidate"
    INCONCLUSIVE = "inconclusive"


@dataclass(frozen=True, slots=True)
class DropoutConfig:
    window_ms: int = 20
    min_windows: int = 3
    rms_threshold: float = 0.0001
    max_samples: int = 480_000


@dataclass(frozen=True, slots=True)
class DropoutReport:
    state: DropoutState
    window_samples: int | None
    analyzed_windows: int
    dropout_windows: int
    longest_dropout_windows: int
    dropout_start_samples: tuple[int, ...]
    reasons: tuple[str, ...]
    limitations: tuple[str, ...]


class TransportContextState(StrEnum):
    STABLE = "stable"
    CHANGED = "changed"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class TransportContextReport:
    state: TransportContextState
    changed_fields: tuple[str, ...]
    err_delta: int | None
    reason: str
    limitations: tuple[str, ...]


DEFAULT_DROPOUT_CONFIG = DropoutConfig()


def detect_dropouts(
    samples: Sequence[float], sample_rate: int, config: DropoutConfig | None = None
) -> DropoutReport:
    """Flag sustained low-RMS windows as dropout candidates, not confirmed faults."""
    config = config or DEFAULT_DROPOUT_CONFIG
    invalid_config = (
        isinstance(config.window_ms, bool)
        or not isinstance(config.window_ms, int)
        or config.window_ms < 1
        or isinstance(config.min_windows, bool)
        or not isinstance(config.min_windows, int)
        or config.min_windows < 1
        or isinstance(config.max_samples, bool)
        or not isinstance(config.max_samples, int)
        or config.max_samples < 1
        or isinstance(config.rms_threshold, bool)
        or not isinstance(config.rms_threshold, (int, float))
        or not isfinite(config.rms_threshold)
        or config.rms_threshold <= 0.0
    )
    if (
        isinstance(sample_rate, bool)
        or not isinstance(sample_rate, int)
        or sample_rate <= 0
        or invalid_config
        or len(samples) < 1
        or len(samples) > config.max_samples
    ):
        return DropoutReport(
            DropoutState.INCONCLUSIVE,
            None,
            0,
            0,
            0,
            (),
            ("invalid rate, configuration, or bounded PCM sample input",),
            ("dropout detection is an amplitude heuristic and cannot confirm signal loss",),
        )
    values: list[float] = []
    for sample in samples:
        if isinstance(sample, bool):
            return DropoutReport(
                DropoutState.INCONCLUSIVE,
                None,
                0,
                0,
                0,
                (),
                ("boolean sample invalid",),
                ("dropout detection is an amplitude heuristic and cannot confirm signal loss",),
            )
        try:
            value = float(sample)
        except (TypeError, ValueError, OverflowError):
            return DropoutReport(
                DropoutState.INCONCLUSIVE,
                None,
                0,
                0,
                0,
                (),
                ("non-numeric sample",),
                ("dropout detection is an amplitude heuristic and cannot confirm signal loss",),
            )
        if not isfinite(value):
            return DropoutReport(
                DropoutState.INCONCLUSIVE,
                None,
                0,
                0,
                0,
                (),
                ("non-finite sample detected",),
                ("dropout detection is an amplitude heuristic and cannot confirm signal loss",),
            )
        values.append(value)
    window_samples = max(1, round(sample_rate * config.window_ms / 1000.0))
    complete_windows = len(samples) // window_samples
    if complete_windows < config.min_windows:
        return DropoutReport(
            DropoutState.INCONCLUSIVE,
            window_samples,
            complete_windows,
            0,
            0,
            (),
            ("too few complete windows to evaluate dropout candidates",),
            ("partial final window is ignored; low amplitude may be intentional",),
        )
    rms_values = [
        sqrt(
            sum(value * value for value in samples[start : start + window_samples]) / window_samples
        )
        for start in range(0, complete_windows * window_samples, window_samples)
    ]
    low = [value <= config.rms_threshold for value in rms_values]
    starts: list[int] = []
    total_dropout_windows = longest = current = 0
    for index, is_low in enumerate(low):
        if is_low:
            current += 1
            longest = max(longest, current)
        else:
            if current >= config.min_windows:
                starts.append((index - current) * window_samples)
                total_dropout_windows += current
            current = 0
    if current >= config.min_windows:
        starts.append((complete_windows - current) * window_samples)
        total_dropout_windows += current
    state = DropoutState.CANDIDATE if starts else DropoutState.NO_CANDIDATE
    return DropoutReport(
        state,
        window_samples,
        complete_windows,
        total_dropout_windows,
        longest,
        tuple(starts),
        (
            (f"{len(starts)} sustained low-amplitude region(s) found",)
            if starts
            else ("no sustained low-amplitude region found",)
        ),
        (
            "amplitude-based dropout candidates are not confirmed faults",
            "partial final window is ignored",
        ),
    )


def detect_transport_context(
    before: TransportSnapshot | None, after: TransportSnapshot | None
) -> TransportContextReport:
    """Compare snapshots and report transport context as stable/changed/unknown."""
    delta = compare_transport(before, after)
    if delta.state is TransportState.NODE_CHANGED:
        return TransportContextReport(
            TransportContextState.CHANGED,
            ("node_id",),
            None,
            delta.reason,
            ("a node change prevents continuous-counter comparison",),
        )
    if delta.state is TransportState.COUNTER_RESET:
        return TransportContextReport(
            TransportContextState.CHANGED,
            ("err_count_reset",),
            None,
            delta.reason,
            ("counter reset prevents interpreting a negative delta",),
        )
    if delta.reason == "node role differs":
        return TransportContextReport(
            TransportContextState.CHANGED,
            ("role",),
            None,
            delta.reason,
            ("role changes may indicate a different transport relationship",),
        )
    if delta.state is not TransportState.VERIFIED or before is None or after is None:
        return TransportContextReport(
            TransportContextState.UNKNOWN,
            (),
            None,
            delta.reason,
            ("transport snapshots do not prove audio continuity",),
        )
    changed: list[str] = []
    if before.node_id != after.node_id:
        changed.append("node_id")
    if before.role != after.role:
        changed.append("role")
    if before.sample_rate != after.sample_rate:
        changed.append("sample_rate")
    if before.quantum != after.quantum:
        changed.append("quantum")
    if delta.err_delta is not None and delta.err_delta > 0:
        changed.append("err_count")
    state = TransportContextState.CHANGED if changed else TransportContextState.STABLE
    return TransportContextReport(
        state,
        tuple(changed),
        delta.err_delta,
        "transport metadata or ERR counter changed" if changed else "comparable snapshots match",
        ("context changes correlate with observations but do not prove causality",),
    )


@dataclass(frozen=True, slots=True)
class SignalConfig:
    min_samples: int = 256
    max_samples: int = 480_000  # 10 s at 48 kHz
    clip_threshold: float = 0.995
    near_zero_threshold: float = 1e-6
    transient_mad_multiplier: float = 8.0
    transient_floor: float = 0.05
    max_transient_candidates: int = 32


DEFAULT_CONFIG = SignalConfig()


def _estimate_true_peak_4x(values: Sequence[float]) -> float:
    """Four-times oversampled Catmull-Rom interpolation; estimate, not certified."""
    peak = max((abs(value) for value in values), default=0.0)
    if len(values) < 4:
        return peak
    for index in range(1, len(values) - 2):
        p0, p1, p2, p3 = values[index - 1 : index + 3]
        for t in (0.25, 0.5, 0.75):
            t2, t3 = t * t, t * t * t
            interpolated = 0.5 * (
                2.0 * p1
                + (-p0 + p2) * t
                + (2.0 * p0 - 5.0 * p1 + 4.0 * p2 - p3) * t2
                + (-p0 + 3.0 * p1 - 3.0 * p2 + p3) * t3
            )
            peak = max(peak, abs(interpolated))
    return peak


def _positive_crossing_frequency(values: Sequence[float], sample_rate: int) -> float | None:
    """Estimate frequency using interpolated rising zero crossings."""
    if len(values) < 4:
        return None
    mean = sum(values) / len(values)
    centered = [value - mean for value in values]
    crossings: list[float] = []
    for index, (left, right) in enumerate(pairwise(centered)):
        if left <= 0.0 < right:
            fraction = -left / (right - left) if right != left else 0.0
            crossings.append(index + fraction)
    if len(crossings) < 3:
        return None
    period = median(crossings[i] - crossings[i - 1] for i in range(1, len(crossings)))
    return sample_rate / period if period > 0.0 else None


def detect_stable_tone(values: Sequence[float], sample_rate: int) -> StableToneMetrics:
    """Detect a stable spectral-line candidate; never label it as mains hum."""
    if (
        len(values) < 512
        or isinstance(sample_rate, bool)
        or not isinstance(sample_rate, int)
        or sample_rate <= 0
    ):
        return StableToneMetrics("INCONCLUSIVE", None, None, "low")
    rms = sqrt(sum(value * value for value in values) / len(values))
    if rms < 0.005:
        return StableToneMetrics("NO_STABLE_TONE", None, None, "low")
    midpoint = len(values) // 2
    estimates = (
        _positive_crossing_frequency(values[:midpoint], sample_rate),
        _positive_crossing_frequency(values[midpoint:], sample_rate),
    )
    if any(
        frequency is None or frequency < 20.0 or frequency > sample_rate / 2.0
        for frequency in estimates
    ):
        return StableToneMetrics("NO_STABLE_TONE", None, None, "low")
    first, second = estimates
    if first is None or second is None:
        return StableToneMetrics("NO_STABLE_TONE", None, None, "low")
    mean_frequency = (first + second) / 2.0
    variation = abs(first - second) / mean_frequency * 100.0 if mean_frequency else None
    if variation is None or variation > 2.5:
        return StableToneMetrics("NO_STABLE_TONE", mean_frequency, variation, "low")
    return StableToneMetrics("UNIDENTIFIED_TONE", mean_frequency, variation, "low")


def _tone_phase(values: Sequence[float], sample_rate: int, frequency: float) -> float:
    mean = sum(values) / len(values)
    cos_sum = sin_sum = 0.0
    for index, value in enumerate(values):
        angle = 2.0 * 3.141592653589793 * frequency * index / sample_rate
        centered = value - mean
        cos_sum += centered * cos(angle)
        sin_sum += centered * sin(angle)
    return atan2(-sin_sum, cos_sum)


def _runs(mask: Sequence[bool]) -> tuple[int, int]:
    """Return (run count, longest run) of True values."""
    count = longest = current = 0
    for flag in mask:
        if flag:
            current += 1
            longest = max(longest, current)
        else:
            if current:
                count += 1
            current = 0
    if current:
        count += 1
    return count, longest


def analyze_pcm(
    samples: Sequence[float], sample_rate: int, config: SignalConfig | None = None
) -> SignalReport:
    """Analyze one bounded normalized mono PCM buffer without side effects."""
    config = config or DEFAULT_CONFIG
    if (
        isinstance(config.min_samples, bool)
        or not isinstance(config.min_samples, int)
        or isinstance(config.max_samples, bool)
        or not isinstance(config.max_samples, int)
        or isinstance(config.max_transient_candidates, bool)
        or not isinstance(config.max_transient_candidates, int)
        or config.min_samples < 1
        or config.max_samples < config.min_samples
        or isinstance(config.clip_threshold, bool)
        or not isinstance(config.clip_threshold, (int, float))
        or not isfinite(config.clip_threshold)
        or not 0.0 < config.clip_threshold <= 1.0
        or isinstance(config.near_zero_threshold, bool)
        or not isinstance(config.near_zero_threshold, (int, float))
        or not isfinite(config.near_zero_threshold)
        or not 0.0 <= config.near_zero_threshold < config.clip_threshold
        or isinstance(config.transient_mad_multiplier, bool)
        or not isinstance(config.transient_mad_multiplier, (int, float))
        or not isfinite(config.transient_mad_multiplier)
        or config.transient_mad_multiplier <= 0.0
        or isinstance(config.transient_floor, bool)
        or not isinstance(config.transient_floor, (int, float))
        or not isfinite(config.transient_floor)
        or config.transient_floor <= 0.0
        or config.max_transient_candidates < 0
    ):
        return SignalReport(SignalState.INCONCLUSIVE, None, ("invalid analysis configuration",), ())
    if isinstance(sample_rate, bool) or not isinstance(sample_rate, int) or sample_rate <= 0:
        return SignalReport(SignalState.INCONCLUSIVE, None, ("invalid sample rate",), ())
    n = len(samples)
    if n < config.min_samples:
        return SignalReport(
            SignalState.INCONCLUSIVE, None, ("sample buffer too short",), ("insufficient samples",)
        )
    if n > config.max_samples:
        return SignalReport(
            SignalState.INCONCLUSIVE,
            None,
            ("sample buffer exceeds configured bound",),
            (f"maximum accepted sample count is {config.max_samples}",),
        )
    values: list[float] = []
    for item in samples:
        if isinstance(item, bool):
            return SignalReport(SignalState.INCONCLUSIVE, None, ("boolean sample invalid",), ())
        try:
            value = float(item)
        except (TypeError, ValueError, OverflowError):
            return SignalReport(SignalState.INCONCLUSIVE, None, ("non-numeric sample",), ())
        if not isfinite(value):
            return SignalReport(SignalState.INCONCLUSIVE, None, ("non-finite sample detected",), ())
        values.append(value)

    dc = sum(values) / n
    square_mean = sum(v * v for v in values) / n
    rms = sqrt(square_mean)
    peak = max(abs(v) for v in values)
    crest = 20.0 * log10(peak / rms) if rms > 0.0 and peak > 0.0 else None
    differences = [values[i] - values[i - 1] for i in range(1, n)]
    difference_rms = (
        sqrt(sum(d * d for d in differences) / len(differences)) if differences else 0.0
    )
    ratio = difference_rms / rms if rms > 0.0 else 0.0

    clip_mask = [abs(v) >= config.clip_threshold for v in values]
    clip_runs, longest_clip = _runs(clip_mask)
    zero_mask = [abs(v) <= config.near_zero_threshold for v in values]
    _zero_runs, longest_zero = _runs(zero_mask)

    # Robust candidate detector on adjacent-sample changes. It is intentionally
    # conservative and emits candidates only; it is not a click/crackle verdict.
    abs_diff = [abs(d) for d in differences]
    candidate_indices: list[int] = []
    if abs_diff:
        baseline = median(abs_diff)
        deviations = [abs(d - baseline) for d in abs_diff]
        mad = median(deviations)
        robust_scale = max(1.4826 * mad, config.transient_floor)
        threshold = baseline + config.transient_mad_multiplier * robust_scale
        candidate_indices = [i + 1 for i, d in enumerate(abs_diff) if d > threshold]
        # Keep candidates separated by at least two samples; retain the larger
        # change when they are adjacent, and cap output size deterministically.
        separated: list[int] = []
        for idx in candidate_indices:
            if separated and idx - separated[-1] <= 2:
                if abs_diff[idx - 1] > abs_diff[separated[-1] - 1]:
                    separated[-1] = idx
            else:
                separated.append(idx)
        candidate_indices = separated[: config.max_transient_candidates]

    metrics = SignalMetrics(
        sample_count=n,
        sample_rate=sample_rate,
        duration_seconds=n / sample_rate,
        rms=rms,
        peak=peak,
        dc_offset=dc,
        crest_factor_db=crest,
        difference_rms=difference_rms,
        difference_ratio=ratio,
        clipped_sample_count=sum(clip_mask),
        clipped_runs=clip_runs,
        longest_clip_run=longest_clip,
        near_zero_sample_count=sum(zero_mask),
        longest_near_zero_run=longest_zero,
        transient_candidates=len(candidate_indices),
        transient_candidate_indices=tuple(candidate_indices),
        true_peak_estimate=_estimate_true_peak_4x(values),
        stable_tone=detect_stable_tone(values, sample_rate),
    )

    if peak <= config.near_zero_threshold:
        return SignalReport(
            SignalState.NO_SAMPLE,
            metrics,
            ("buffer contains no meaningful signal energy",),
            ("silence is not a quality verdict",),
        )
    reasons: list[str] = []
    if clip_runs:
        reasons.append("clipping candidate samples present")
    if candidate_indices:
        reasons.append("transient candidates detected")
    state = SignalState.SUSPECT if reasons else SignalState.HEALTHY_OBSERVED
    return SignalReport(
        state,
        metrics,
        tuple(reasons),
        (
            "transient thresholds are heuristic and uncalibrated",
            "single mono tap cannot localize a fault",
            "digital samples cannot establish analog distortion or perceived quality",
        ),
    )


def analyze_stereo_pcm(
    left_samples: Sequence[float],
    right_samples: Sequence[float],
    sample_rate: int,
    config: SignalConfig | None = None,
) -> StereoReport:
    """Analyze stereo correlation, tone phase, and estimated inter-sample peaks."""
    left_report = analyze_pcm(left_samples, sample_rate, config)
    right_report = analyze_pcm(right_samples, sample_rate, config)
    if len(left_samples) != len(right_samples):
        return StereoReport(
            left_report, right_report, None, ("stereo buffers have unequal lengths",)
        )
    if left_report.metrics is None or right_report.metrics is None:
        return StereoReport(left_report, right_report, None, ("stereo analysis is inconclusive",))
    count = len(left_samples)
    left_mean = sum(left_samples) / count
    right_mean = sum(right_samples) / count
    left_centered = [sample - left_mean for sample in left_samples]
    right_centered = [sample - right_mean for sample in right_samples]
    left_energy = sum(sample * sample for sample in left_centered)
    right_energy = sum(sample * sample for sample in right_centered)
    correlation = (
        sum(a * b for a, b in zip(left_centered, right_centered, strict=True))
        / sqrt(left_energy * right_energy)
        if left_energy > 0.0 and right_energy > 0.0
        else None
    )
    left_tone = left_report.metrics.stable_tone
    right_tone = right_report.metrics.stable_tone
    phase: float | None = None
    if (
        left_tone.frequency_hz is not None
        and right_tone.frequency_hz is not None
        and abs(left_tone.frequency_hz - right_tone.frequency_hz)
        / max(left_tone.frequency_hz, right_tone.frequency_hz)
        <= 0.02
    ):
        frequency = (left_tone.frequency_hz + right_tone.frequency_hz) / 2.0
        difference = _tone_phase(right_samples, sample_rate, frequency) - _tone_phase(
            left_samples, sample_rate, frequency
        )
        phase = (difference + 3.141592653589793) % (2.0 * 3.141592653589793) - 3.141592653589793
    left_peak = left_report.metrics.true_peak_estimate
    right_peak = right_report.metrics.true_peak_estimate
    peak_difference = (
        20.0 * log10(right_peak / left_peak) if left_peak > 0.0 and right_peak > 0.0 else None
    )
    metrics = StereoMetrics(
        sample_count=count,
        sample_rate=sample_rate,
        correlation=correlation,
        phase_difference_degrees=(phase * 180.0 / 3.141592653589793 if phase is not None else None),
        phase_confidence="low",
        left_true_peak=left_peak,
        right_true_peak=right_peak,
        peak_difference_db=peak_difference,
    )
    return StereoReport(left_report, right_report, metrics, ())


def compare_taps(raw: SignalReport, processed: SignalReport) -> TapComparison:
    """Compare two tap reports; classify location only as a cautious hypothesis."""
    a, b = raw.metrics, processed.metrics
    if a is None or b is None or a.sample_count != b.sample_count or a.sample_rate != b.sample_rate:
        return TapComparison(
            raw, processed, "UNLOCALIZED", None, "low", ("missing or unequal tap coverage",)
        )
    ratio = b.rms / a.rms if a.rms > 0 else None
    raw_suspect = raw.state is SignalState.SUSPECT
    post_suspect = processed.state is SignalState.SUSPECT
    if raw_suspect and not post_suspect:
        location = "RAW_ONLY"
    elif post_suspect and not raw_suspect:
        location = "PROCESSING_PATH"
    elif raw_suspect and post_suspect:
        location = "BOTH_TAPS"
    else:
        location = "NO_CANDIDATE"
    return TapComparison(
        raw,
        processed,
        location,
        ratio,
        "low",
        (
            "heuristic candidate flags are not validated fault labels",
            "tap timing/content alignment is not verified",
        ),
    )


def compare_transport(
    before: TransportSnapshot | None, after: TransportSnapshot | None
) -> TransportDelta:
    """Compute safe transport ERR deltas only for comparable same-node samples."""
    if before is None or after is None:
        return TransportDelta(TransportState.UNKNOWN, None, None, None, "missing snapshot")
    if (
        not isinstance(before.node_id, str)
        or not before.node_id
        or not isinstance(after.node_id, str)
        or not after.node_id
        or not isinstance(before.role, str)
        or not before.role
        or not isinstance(after.role, str)
        or not after.role
        or isinstance(before.err_count, bool)
        or not isinstance(before.err_count, int)
        or isinstance(after.err_count, bool)
        or not isinstance(after.err_count, int)
        or (
            before.sample_rate is not None
            and (
                not isinstance(before.sample_rate, int)
                or isinstance(before.sample_rate, bool)
                or before.sample_rate <= 0
            )
        )
        or (
            after.sample_rate is not None
            and (
                not isinstance(after.sample_rate, int)
                or isinstance(after.sample_rate, bool)
                or after.sample_rate <= 0
            )
        )
        or (
            before.quantum is not None
            and (
                not isinstance(before.quantum, int)
                or isinstance(before.quantum, bool)
                or before.quantum <= 0
            )
        )
        or (
            after.quantum is not None
            and (
                not isinstance(after.quantum, int)
                or isinstance(after.quantum, bool)
                or after.quantum <= 0
            )
        )
    ):
        return TransportDelta(TransportState.UNKNOWN, None, None, None, "invalid snapshot fields")
    if before.role != after.role:
        return TransportDelta(TransportState.UNKNOWN, None, None, None, "node role differs")
    if before.node_id != after.node_id:
        return TransportDelta(
            TransportState.NODE_CHANGED, None, None, None, "node identity changed"
        )
    if before.err_count < 0 or after.err_count < 0:
        return TransportDelta(TransportState.UNKNOWN, None, None, None, "invalid ERR counter")
    if after.err_count < before.err_count:
        return TransportDelta(
            TransportState.COUNTER_RESET, None, None, None, "ERR counter decreased/reset"
        )
    return TransportDelta(
        TransportState.VERIFIED,
        after.err_count - before.err_count,
        before.sample_rate != after.sample_rate,
        before.quantum != after.quantum,
        "same-node counter comparison",
    )
