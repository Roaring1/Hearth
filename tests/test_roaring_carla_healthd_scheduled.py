"""Scheduled healthd cycle must collect and persist transport evidence."""

from __future__ import annotations

import copy
import importlib
import sys
import types
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))
try:
    import numpy  # noqa: F401
except ImportError:
    # The scheduled-path test exercises orchestration, not NumPy DSP routines.
    # Production installs provide NumPy; CI's core test extra intentionally does not.
    sys.modules["numpy"] = types.ModuleType("numpy")
healthd = importlib.import_module("roaring_carla_healthd")


def _transport(errors: int, at: float) -> dict:
    return {
        "nodes": {
            "scarlett": {
                "id": 101,
                "name": "scarlett",
                "status": "R",
                "quantum": 512,
                "rate": 48_000,
                "errors": errors,
                "wait": "0.01",
                "busy": "0.05",
                "wq": "0.02",
                "bq": "0.10",
            }
        },
        "quantum": 512,
        "rate": 48_000,
        "sampled_at_unix": 1_700_000_000 + at,
        "sampled_at_monotonic": at,
        "pw_top_ok": True,
        "metadata_ok": True,
        "errors": [],
    }


def test_scheduled_cycle_takes_pre_post_transport_snapshots(monkeypatch) -> None:
    prior = healthd.snapshot_for_state(_transport(4, 0.0))
    current_state = {"scheduled_transport_snapshot": prior}
    written: dict = {}
    records: list[dict] = []
    samples = iter([_transport(5, 1.0), _transport(7, 9.0)])
    sample = {
        "sample_seconds": 8.0,
        "windows": 20,
        "active_windows": 10,
        "valid_windows": 10,
        "candidate_windows": 0,
        "dropout_windows": 0,
        "fault_confirmed": False,
        "raw_rms_p50": 0.01,
        "raw_rms_p90": 0.02,
        "raw_rms_p99": 0.03,
        "post_rms_p50": 0.01,
        "post_rms_p90": 0.02,
        "post_rms_p99": 0.03,
        "correlation_p50": 0.9,
        "capture_error": None,
        "muted": False,
        "verdict": "CORRECT",
        "reason": "sufficient aligned active signal",
    }

    monkeypatch.setattr(healthd, "_read_state", lambda: copy.deepcopy(current_state))
    monkeypatch.setattr(healthd, "_write_state", lambda state: written.update(copy.deepcopy(state)))
    monkeypatch.setattr(
        healthd, "_record_sample", lambda summary: records.append(copy.deepcopy(summary))
    )
    monkeypatch.setattr(healthd, "_b1_expected_active", lambda: True)
    monkeypatch.setattr(healthd, "capture_sample", lambda seconds: copy.deepcopy(sample))
    monkeypatch.setattr(healthd, "_read_pipewire_health", lambda: next(samples))

    assert healthd.run_sample_cycle(8.0, {"AUTO_RECOVER": False, "NOTIFY": False}) == 0

    assert written["scheduled_transport_snapshot"]["nodes"]["scarlett"]["errors"] == 7
    stored = written["last_sample"]["transport"]
    assert stored["since_previous_sample"]["node_deltas"] == {"scarlett": 1}
    assert stored["during_sample"]["node_deltas"] == {"scarlett": 2}
    assert stored["during_sample"]["elapsed_seconds"] == 8.0
    assert records[0]["transport"] == stored


def test_confirmed_signal_fault_does_not_restart_when_transport_errors_also_increase(
    monkeypatch,
) -> None:
    prior = healthd.snapshot_for_state(_transport(5, 0.0))
    current_state = {"scheduled_transport_snapshot": prior}
    written: dict = {}
    records: list[dict] = []
    notices: list[tuple] = []
    samples = iter([_transport(5, 1.0), _transport(6, 9.0)])
    fault_sample = {
        "sample_seconds": 8.0,
        "windows": 20,
        "active_windows": 10,
        "valid_windows": 10,
        "candidate_windows": 3,
        "dropout_windows": 0,
        "fault_confirmed": True,
        "raw_rms_p50": 0.01,
        "raw_rms_p90": 0.02,
        "raw_rms_p99": 0.03,
        "post_rms_p50": 0.01,
        "post_rms_p90": 0.02,
        "post_rms_p99": 0.03,
        "correlation_p50": 0.9,
        "capture_error": None,
        "muted": False,
        "verdict": "INCORRECT",
        "reason": "confirmed synthetic fault",
    }

    monkeypatch.setattr(healthd, "_read_state", lambda: copy.deepcopy(current_state))
    monkeypatch.setattr(healthd, "_write_state", lambda state: written.update(copy.deepcopy(state)))
    monkeypatch.setattr(
        healthd,
        "_record_sample",
        lambda summary: records.append(copy.deepcopy(summary)),
    )
    monkeypatch.setattr(healthd, "_b1_expected_active", lambda: True)
    monkeypatch.setattr(healthd, "capture_sample", lambda seconds: copy.deepcopy(fault_sample))
    monkeypatch.setattr(healthd, "_read_pipewire_health", lambda: next(samples))
    monkeypatch.setattr(
        healthd,
        "handle_confirmed_fault",
        lambda config, state: (_ for _ in ()).throw(
            AssertionError("Carla restart must be withheld")
        ),
    )
    monkeypatch.setattr(
        healthd,
        "notify",
        lambda *args, **kwargs: notices.append((args, kwargs)),
    )

    assert healthd.run_sample_cycle(8.0, {"AUTO_RECOVER": True, "NOTIFY": True}) == 0

    assert written["last_verdict"] == "INCORRECT_TRANSPORT_CONFLICT"
    assert written["last_sample"]["transport"]["during_sample"]["node_deltas"] == {"scarlett": 1}
    assert notices
    assert "restart was withheld" in notices[0][0][1]


def test_capture_coverage_uses_minimum_of_raw_and_processed_frames() -> None:
    block = healthd.np.zeros(healthd.BLOCK_FRAMES, dtype=healthd.np.float32)
    raw = [block for _ in range(80)]
    processed = [block for _ in range(60)]
    result = healthd._capture_coverage(8.0, raw, processed)
    assert result["expected_frames"] == 8 * 48_000
    assert result["raw_frames"] == 80 * healthd.BLOCK_FRAMES
    assert result["processed_frames"] == 60 * healthd.BLOCK_FRAMES
    assert result["paired_frames"] == 60 * healthd.BLOCK_FRAMES
    assert result["fraction"] == 0.75


def test_low_paired_coverage_is_inconclusive_even_with_confirmed_fault() -> None:
    result = {
        "capture_coverage": {"fraction": 0.5},
        "fault_confirmed": True,
        "active_windows": 20,
        "valid_windows": 20,
    }
    verdict, reason = healthd.classify_sample(result)
    assert verdict == "INCONCLUSIVE"
    assert "coverage" in reason


def test_missing_coverage_is_not_silently_treated_as_a_healthy_sample() -> None:
    verdict, reason = healthd.classify_sample(
        {
            "fault_confirmed": False,
            "active_windows": 20,
            "valid_windows": 20,
        }
    )
    assert verdict == "INCONCLUSIVE"
    assert "coverage" in reason


def test_sufficient_coverage_preserves_existing_quality_verdicts() -> None:
    coverage = {"fraction": 1.0}
    correct, _ = healthd.classify_sample(
        {
            "capture_coverage": coverage,
            "fault_confirmed": False,
            "active_windows": 20,
            "valid_windows": 20,
            "candidate_windows": 0,
            "dropout_windows": 0,
        }
    )
    incorrect, _ = healthd.classify_sample(
        {
            "capture_coverage": coverage,
            "fault_confirmed": True,
            "active_windows": 20,
            "valid_windows": 20,
        }
    )
    assert correct == "CORRECT"
    assert incorrect == "INCORRECT"
