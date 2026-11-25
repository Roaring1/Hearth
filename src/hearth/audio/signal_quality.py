"""Pure-Python, bounded, measurement-only signal-quality primitives.

The engine consumes normalized mono PCM samples. It never captures audio,
starts subprocesses, queries PipeWire, persists samples, or performs recovery.
Thresholds here produce *candidates*, not validated fault verdicts.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from math import isfinite, log10, sqrt
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
        config.min_samples < 1
        or config.max_samples < config.min_samples
        or not 0.0 < config.clip_threshold <= 1.0
        or config.near_zero_threshold < 0.0
        or config.transient_mad_multiplier <= 0.0
        or config.transient_floor < 0.0
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
        not before.node_id
        or not after.node_id
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
