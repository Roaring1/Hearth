"""Numeric, user-confirmed baseline for B1 low-energy noise measurements.

No PCM is stored. A baseline is never enrolled automatically or updated by the
periodic health sampler.
"""

from __future__ import annotations

import json
import math
import tempfile
from pathlib import Path
from statistics import median
from typing import Any

BASELINE_SCHEMA_VERSION = 1
BASELINE_PROFILE_ID = "b1-sm7b-carla-monitor-48k-v1"
REQUIRED_SAMPLE_COUNT = 3
MIN_CAPTURE_COVERAGE = 0.90
MIN_VALID_FLOOR_FRAMES = 20
NOISE_RISE_THRESHOLD_DB = 8.0
TONE_MATCH_TOLERANCE_HZ = 2.5
MAX_PROFILE_BYTES = 64 * 1024
MAX_PROFILE_TONES_PER_TAP = 24
MIN_TONE_HZ = 20.0
MAX_TONE_HZ = 1000.0


def _numeric(value: Any) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(float(value))


def _tone_frequencies(tap: dict[str, Any]) -> list[float]:
    tones = tap.get("persistent_tones") or []
    result = []
    for tone in tones:
        frequency = tone.get("frequency_hz")
        if _numeric(frequency):
            result.append(float(frequency))
    return result


def _consensus_tones(samples: list[list[float]]) -> list[dict[str, float | int]]:
    clusters: list[dict[str, Any]] = []
    for sample_index, frequencies in enumerate(samples):
        for frequency in frequencies:
            match = next(
                (
                    cluster
                    for cluster in clusters
                    if abs(median(cluster["frequencies"]) - frequency) <= TONE_MATCH_TOLERANCE_HZ
                ),
                None,
            )
            if match is None:
                clusters.append({"frequencies": [frequency], "sample_indices": {sample_index}})
            else:
                match["frequencies"].append(frequency)
                match["sample_indices"].add(sample_index)
    consensus = [
        {
            "frequency_hz": round(float(median(cluster["frequencies"])), 2),
            "sample_count": len(cluster["sample_indices"]),
        }
        for cluster in clusters
        if len(cluster["sample_indices"]) >= 2
    ]
    return sorted(consensus, key=lambda tone: tone["frequency_hz"])


def build_noise_baseline(
    samples: list[dict[str, Any]], *, created_at: float, confirmed_quiet: bool = False
) -> dict[str, Any]:
    if not confirmed_quiet:
        raise ValueError("baseline enrollment requires explicit quiet confirmation")
    if len(samples) != REQUIRED_SAMPLE_COUNT:
        raise ValueError(f"baseline enrollment requires exactly {REQUIRED_SAMPLE_COUNT} samples")
    metrics_by_tap: dict[str, dict[str, list[float]]] = {
        "raw": {"rms_dbfs": [], "5k_12k_dbfs": [], "5k_12k_flatness": []},
        "processed": {"rms_dbfs": [], "5k_12k_dbfs": [], "5k_12k_flatness": []},
    }
    tones_by_tap: dict[str, list[list[float]]] = {"raw": [], "processed": []}
    for index, sample in enumerate(samples):
        if sample.get("capture_error"):
            raise ValueError(f"sample {index + 1} capture failed")
        if sample.get("verdict") not in {"CORRECT", "NO_SAMPLE"}:
            raise ValueError(f"sample {index + 1} is not eligible for baseline enrollment")
        coverage = (sample.get("capture_coverage") or {}).get("fraction")
        if not _numeric(coverage) or float(coverage) < MIN_CAPTURE_COVERAGE:
            raise ValueError(f"sample {index + 1} capture coverage is insufficient")
        if sample.get("fault_confirmed"):
            raise ValueError(f"sample {index + 1} contains a confirmed fault")
        features = sample.get("signal_features")
        if not isinstance(features, dict):
            raise ValueError(f"sample {index + 1} lacks signal features")
        for tap in ("raw", "processed"):
            tap_metrics = features.get(tap) or {}
            low_energy = tap_metrics.get("low_energy") or {}
            if (
                low_energy.get("noise_floor_valid") is not True
                or not _numeric(low_energy.get("rms_dbfs"))
                or not _numeric(low_energy.get("5k_12k_dbfs"))
                or not _numeric(low_energy.get("5k_12k_flatness"))
                or int(low_energy.get("frames", 0)) < MIN_VALID_FLOOR_FRAMES
            ):
                raise ValueError(f"sample {index + 1} {tap} noise floor is unverified")
            for metric in ("rms_dbfs", "5k_12k_dbfs", "5k_12k_flatness"):
                metrics_by_tap[tap][metric].append(float(low_energy[metric]))
            tones_by_tap[tap].append(_tone_frequencies(tap_metrics))

    metrics: dict[str, dict[str, float | list[dict[str, float | int]]]] = {}
    for tap, fields in metrics_by_tap.items():
        metrics[tap] = {
            metric: round(float(median(values)), 3) for metric, values in fields.items()
        }
        metrics[tap]["persistent_tones"] = _consensus_tones(tones_by_tap[tap])
    return {
        "schema_version": BASELINE_SCHEMA_VERSION,
        "profile_id": BASELINE_PROFILE_ID,
        "created_at": float(created_at),
        "sample_count": REQUIRED_SAMPLE_COUNT,
        "capture_seconds_per_sample": 8.0,
        "minimum_floor_frames": MIN_VALID_FLOOR_FRAMES,
        "metrics": metrics,
        "auto_update": False,
        "pcm_saved": False,
        "user_confirmed_quiet": True,
    }


def _valid_noise_baseline(profile: dict[str, Any]) -> bool:
    if (
        profile.get("schema_version") != BASELINE_SCHEMA_VERSION
        or profile.get("profile_id") != BASELINE_PROFILE_ID
        or profile.get("auto_update") is not False
        or profile.get("sample_count") != REQUIRED_SAMPLE_COUNT
        or profile.get("user_confirmed_quiet") is not True
        or profile.get("pcm_saved") is not False
    ):
        return False
    if not _numeric(profile.get("created_at")):
        return False
    metrics = profile.get("metrics")
    if not isinstance(metrics, dict):
        return False
    for tap in ("raw", "processed"):
        values = metrics.get(tap)
        if not isinstance(values, dict):
            return False
        if not all(
            _numeric(values.get(field)) for field in ("rms_dbfs", "5k_12k_dbfs", "5k_12k_flatness")
        ):
            return False
        flatness = float(values["5k_12k_flatness"])
        if not 0.0 <= flatness <= 1.0:
            return False
        tones = values.get("persistent_tones")
        if not isinstance(tones, list) or len(tones) > MAX_PROFILE_TONES_PER_TAP:
            return False
        for tone in tones:
            if not isinstance(tone, dict):
                return False
            frequency = tone.get("frequency_hz")
            sample_count = tone.get("sample_count")
            if (
                not _numeric(frequency)
                or not MIN_TONE_HZ <= float(frequency) <= MAX_TONE_HZ
                or not isinstance(sample_count, int)
                or not 2 <= sample_count <= REQUIRED_SAMPLE_COUNT
            ):
                return False
    return True


def compare_noise_baseline(
    sample: dict[str, Any], baseline: dict[str, Any] | None
) -> dict[str, Any]:
    if baseline is None:
        return {"status": "baseline_missing", "candidate": False, "automatic_action": "none"}
    if not _valid_noise_baseline(baseline):
        return {"status": "baseline_invalid", "candidate": False, "automatic_action": "none"}
    features = sample.get("signal_features")
    baseline_metrics = baseline.get("metrics")
    if not isinstance(features, dict) or not isinstance(baseline_metrics, dict):
        return {
            "status": "current_features_missing",
            "candidate": False,
            "automatic_action": "none",
        }

    deltas: dict[str, dict[str, float | None]] = {}
    noise_rises: list[str] = []
    new_tones: list[dict[str, str | float]] = []
    taps_valid = True
    for tap in ("raw", "processed"):
        current = features.get(tap) or {}
        low_energy = current.get("low_energy") or {}
        base = baseline_metrics.get(tap) or {}
        if low_energy.get("noise_floor_valid") is not True:
            taps_valid = False
            deltas[tap] = {"rms_dbfs": None, "5k_12k_dbfs": None}
            continue
        current_rms = low_energy.get("rms_dbfs")
        current_high = low_energy.get("5k_12k_dbfs")
        base_rms = base.get("rms_dbfs")
        base_high = base.get("5k_12k_dbfs")
        if not all(_numeric(value) for value in (current_rms, current_high, base_rms, base_high)):
            taps_valid = False
            deltas[tap] = {"rms_dbfs": None, "5k_12k_dbfs": None}
            continue
        rms_delta = round(float(current_rms) - float(base_rms), 3)
        high_delta = round(float(current_high) - float(base_high), 3)
        deltas[tap] = {"rms_dbfs": rms_delta, "5k_12k_dbfs": high_delta}
        if rms_delta >= NOISE_RISE_THRESHOLD_DB:
            noise_rises.append(f"{tap}_rms")
        if high_delta >= NOISE_RISE_THRESHOLD_DB:
            noise_rises.append(f"{tap}_high_band")
        base_tones = _tone_frequencies({"persistent_tones": base.get("persistent_tones")})
        current_tones = _tone_frequencies(current)
        for frequency in current_tones:
            if not any(abs(frequency - known) <= TONE_MATCH_TOLERANCE_HZ for known in base_tones):
                new_tones.append({"tap": tap, "frequency_hz": round(frequency, 2)})

    status = "compared" if taps_valid else "current_floor_unavailable"
    candidate = bool(taps_valid and (noise_rises or new_tones))
    return {
        "status": status,
        "noise_rise_candidates": noise_rises if taps_valid else [],
        "new_persistent_tone_candidates": new_tones if taps_valid else [],
        "deltas_db": deltas,
        "candidate": candidate,
        "threshold_db": NOISE_RISE_THRESHOLD_DB,
        "tone_match_tolerance_hz": TONE_MATCH_TOLERANCE_HZ,
        "automatic_action": "none",
    }


def save_noise_baseline(path: Path, profile: dict[str, Any]) -> None:
    if not _valid_noise_baseline(profile):
        raise ValueError("refusing to save an invalid noise baseline profile")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            json.dump(profile, handle, indent=2, sort_keys=True)
            handle.write("\n")
            temporary_path = Path(handle.name)
        temporary_path.chmod(0o600)
        temporary_path.replace(path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def load_noise_baseline(path: Path) -> dict[str, Any] | None:
    try:
        profile_path = Path(path)
        if profile_path.stat().st_size > MAX_PROFILE_BYTES:
            return {"status": "baseline_invalid"}
        value = json.loads(profile_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError):
        return {"status": "baseline_invalid"}
    return value if isinstance(value, dict) else {"status": "baseline_invalid"}
