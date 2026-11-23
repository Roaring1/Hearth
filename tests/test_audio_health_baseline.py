from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from audio_health_baseline import (
    build_noise_baseline,
    compare_noise_baseline,
    load_noise_baseline,
    save_noise_baseline,
)


def _sample(rms: float, high: float, tone: float | None = None) -> dict:
    tones = [] if tone is None else [{"frequency_hz": tone, "prominence_db": 20.0}]
    return {
        "capture_error": None,
        "fault_confirmed": False,
        "verdict": "NO_SAMPLE",
        "capture_coverage": {"fraction": 0.98},
        "signal_features": {
            "raw": {
                "low_energy": {
                    "noise_floor_valid": True,
                    "rms_dbfs": rms,
                    "5k_12k_dbfs": high,
                    "5k_12k_flatness": 0.2,
                    "frames": 24,
                },
                "persistent_tones": tones,
            },
            "processed": {
                "low_energy": {
                    "noise_floor_valid": True,
                    "rms_dbfs": rms - 5,
                    "5k_12k_dbfs": high - 5,
                    "5k_12k_flatness": 0.3,
                    "frames": 24,
                },
                "persistent_tones": [],
            },
        },
    }


def test_build_baseline_requires_three_valid_quiet_samples() -> None:
    profile = build_noise_baseline(
        [_sample(-70, -80), _sample(-72, -82), _sample(-71, -81)],
        created_at=123.0,
        confirmed_quiet=True,
    )
    assert profile["sample_count"] == 3
    assert profile["created_at"] == 123.0
    assert profile["metrics"]["raw"]["rms_dbfs"] == -71.0
    assert profile["metrics"]["raw"]["5k_12k_dbfs"] == -81.0
    assert profile["auto_update"] is False
    assert profile["pcm_saved"] is False


def test_build_baseline_rejects_unverified_noise_floor_or_low_coverage() -> None:
    samples = [_sample(-70, -80), _sample(-72, -82), _sample(-71, -81)]
    samples[1]["signal_features"]["raw"]["low_energy"]["noise_floor_valid"] = False
    with pytest.raises(ValueError, match="noise floor is unverified"):
        build_noise_baseline(samples, created_at=1.0, confirmed_quiet=True)
    samples[1]["signal_features"]["raw"]["low_energy"]["noise_floor_valid"] = True
    samples[2]["capture_coverage"]["fraction"] = 0.4
    with pytest.raises(ValueError, match="coverage"):
        build_noise_baseline(samples, created_at=1.0, confirmed_quiet=True)


def test_baseline_comparison_detects_noise_rise_and_new_tone() -> None:
    baseline = build_noise_baseline(
        [_sample(-70, -80), _sample(-72, -82), _sample(-71, -81, 97.0)],
        created_at=1.0,
        confirmed_quiet=True,
    )
    current = _sample(-60, -68, 120.0)
    comparison = compare_noise_baseline(current, baseline)
    assert comparison["status"] == "compared"
    assert comparison["candidate"] is True
    assert "raw_rms" in comparison["noise_rise_candidates"]
    assert "raw_high_band" in comparison["noise_rise_candidates"]
    assert comparison["new_persistent_tone_candidates"] == [{"tap": "raw", "frequency_hz": 120.0}]
    assert comparison["automatic_action"] == "none"


def test_baseline_comparison_does_not_flag_small_changes_or_known_tone() -> None:
    baseline = build_noise_baseline(
        [_sample(-70, -80, 97.0), _sample(-72, -82, 97.5), _sample(-71, -81, 98.0)],
        created_at=1.0,
        confirmed_quiet=True,
    )
    current = _sample(-66, -76, 97.8)
    comparison = compare_noise_baseline(current, baseline)
    assert comparison["status"] == "compared"
    assert comparison["candidate"] is False
    assert comparison["new_persistent_tone_candidates"] == []


def test_baseline_comparison_skips_when_current_floor_is_unavailable() -> None:
    baseline = build_noise_baseline(
        [_sample(-70, -80), _sample(-72, -82), _sample(-71, -81)],
        created_at=1.0,
        confirmed_quiet=True,
    )
    current = _sample(-60, -68)
    current["signal_features"]["processed"]["low_energy"]["noise_floor_valid"] = False
    comparison = compare_noise_baseline(current, baseline)
    assert comparison["status"] == "current_floor_unavailable"
    assert comparison["candidate"] is False
    assert comparison["noise_rise_candidates"] == []


def test_baseline_file_save_load_and_invalid_file(tmp_path: Path) -> None:
    profile = build_noise_baseline(
        [_sample(-70, -80), _sample(-72, -82), _sample(-71, -81)],
        created_at=10.0,
        confirmed_quiet=True,
    )
    path = tmp_path / "noise-baseline.json"
    save_noise_baseline(path, profile)
    assert load_noise_baseline(path) == profile
    if sys.platform == "linux":
        assert path.stat().st_mode & 0o777 == 0o600
    path.write_text("{broken", encoding="utf-8")
    assert load_noise_baseline(path) == {"status": "baseline_invalid"}
    invalid = {"schema_version": 1, "profile_id": "wrong"}
    assert compare_noise_baseline(_sample(-60, -70), invalid)["status"] == "baseline_invalid"


def test_build_baseline_requires_explicit_quiet_confirmation() -> None:
    samples = [_sample(-70, -80), _sample(-72, -82), _sample(-71, -81)]
    with pytest.raises(ValueError, match="explicit quiet confirmation"):
        build_noise_baseline(samples, created_at=1.0)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda profile: profile["metrics"]["raw"]["persistent_tones"].append("malformed"),
        lambda profile: profile["metrics"]["raw"]["persistent_tones"].append(
            {"frequency_hz": float("nan"), "sample_count": 2}
        ),
        lambda profile: profile["metrics"]["raw"]["persistent_tones"].append(
            {"frequency_hz": 1500.0, "sample_count": 2}
        ),
        lambda profile: profile["metrics"]["raw"]["persistent_tones"].append(
            {"frequency_hz": 97.0, "sample_count": 99}
        ),
        lambda profile: profile["metrics"]["raw"].update({"5k_12k_flatness": 1.4}),
        lambda profile: profile.update({"created_at": float("nan")}),
    ],
)
def test_baseline_validator_rejects_malformed_profile_structure(mutate) -> None:
    profile = build_noise_baseline(
        [_sample(-70, -80), _sample(-72, -82), _sample(-71, -81)],
        created_at=1.0,
        confirmed_quiet=True,
    )
    mutate(profile)
    assert compare_noise_baseline(_sample(-60, -70), profile)["status"] == "baseline_invalid"


def test_baseline_loader_rejects_oversized_profile(tmp_path: Path) -> None:
    path = tmp_path / "large.json"
    path.write_bytes(b" " * (64 * 1024 + 1))
    assert load_noise_baseline(path) == {"status": "baseline_invalid"}
