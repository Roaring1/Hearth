"""Scheduled healthd cycle must collect and persist transport evidence."""

from __future__ import annotations

import copy
import importlib
import subprocess
import sys
import types
from pathlib import Path

import pytest

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))
try:
    import numpy  # noqa: F401
except ImportError:
    # The scheduled-path test exercises orchestration, not NumPy DSP routines.
    # Production installs provide NumPy; CI's core test extra intentionally does not.
    numpy_stub = types.ModuleType("numpy")
    numpy_stub.asarray = lambda values, dtype=None: values
    numpy_stub.float64 = float
    sys.modules["numpy"] = numpy_stub
healthd = importlib.import_module("roaring_carla_healthd")


def test_config_defaults_to_silent_and_allows_explicit_notification_opt_in(tmp_path) -> None:
    config_path = tmp_path / "carla-health.env"
    assert healthd.load_config(config_path) == {"AUTO_RECOVER": False, "NOTIFY": False}

    config_path.write_text("NOTIFY=1\nAUTO_RECOVER=0\n", encoding="utf-8")
    assert healthd.load_config(config_path) == {"AUTO_RECOVER": False, "NOTIFY": True}


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
    processed = [block for _ in range(72)]
    result = healthd._capture_coverage(8.0, raw, processed)
    assert result["expected_frames"] == 8 * 48_000
    assert result["raw_frames"] == 80 * healthd.BLOCK_FRAMES
    assert result["processed_frames"] == 72 * healthd.BLOCK_FRAMES
    assert result["paired_frames"] == 72 * healthd.BLOCK_FRAMES
    assert result["fraction"] == 0.9


def test_low_paired_coverage_is_inconclusive_even_with_confirmed_fault() -> None:
    result = {
        "capture_coverage": {"fraction": 0.89},
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


def test_exact_ninety_percent_coverage_is_the_minimum_accepted_boundary() -> None:
    summary = {
        "capture_coverage": {"fraction": 0.90},
        "fault_confirmed": False,
        "active_windows": 20,
        "valid_windows": 20,
        "candidate_windows": 0,
        "dropout_windows": 0,
    }
    verdict, _ = healthd.classify_sample(summary)
    assert verdict == "CORRECT"


def test_alignment_lag_summary_uses_median_and_mad_not_mean() -> None:
    assert healthd._alignment_lag_summary([10, 11, 10, 9, 3000]) == (10, 1.0, 5)
    assert healthd._alignment_lag_summary([]) == (None, None, 0)


def test_no_sample_streak_persists_and_resets_on_activity() -> None:
    state = {"monitoring_streaks": {"no_activity": 2, "inconclusive": 1}}
    sample = {"verdict": "NO_SAMPLE", "active_windows": 0}
    assert healthd._update_monitoring_streaks(state, sample) == {
        "no_activity": 3,
        "inconclusive": 0,
    }
    assert state["monitoring_streaks"] == sample["monitoring_streaks"]

    sample = {"verdict": "CORRECT", "active_windows": healthd.MIN_ACTIVE_WINDOWS}
    assert healthd._update_monitoring_streaks(state, sample) == {
        "no_activity": 0,
        "inconclusive": 0,
    }


def test_inconclusive_streak_tracks_unverified_samples_and_active_signal_clears_no_activity() -> (
    None
):
    state = {"monitoring_streaks": {"no_activity": 5, "inconclusive": 0}}
    sample = {"verdict": "INCONCLUSIVE", "active_windows": healthd.MIN_ACTIVE_WINDOWS}
    assert healthd._update_monitoring_streaks(state, sample) == {
        "no_activity": 0,
        "inconclusive": 1,
    }
    sample = {"verdict": "INCONCLUSIVE", "active_windows": 0}
    assert healthd._update_monitoring_streaks(state, sample) == {
        "no_activity": 0,
        "inconclusive": 2,
    }


def test_monitoring_streak_counters_are_bounded_and_muted_does_not_count() -> None:
    state = {"monitoring_streaks": {"no_activity": 255, "inconclusive": 255}}
    sample = {"verdict": "NO_SAMPLE", "active_windows": 0}
    assert healthd._update_monitoring_streaks(state, sample) == {
        "no_activity": 255,
        "inconclusive": 0,
    }
    sample = {"verdict": "MUTED", "active_windows": 0}
    assert healthd._update_monitoring_streaks(state, sample) == {
        "no_activity": 0,
        "inconclusive": 0,
    }


def test_monitoring_streaks_sanitize_corrupt_or_unexpected_persisted_values() -> None:
    state = {"monitoring_streaks": {"no_activity": "not-a-number", "inconclusive": -4}}
    sample = {"verdict": "NO_SAMPLE", "active_windows": 0}
    assert healthd._update_monitoring_streaks(state, sample) == {
        "no_activity": 1,
        "inconclusive": 0,
    }


def test_capture_command_requests_stereo_when_explicit(monkeypatch) -> None:
    captured: dict = {}

    class FakeProcess:
        stdout = object()

    def fake_popen(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return FakeProcess()

    monkeypatch.setattr(healthd.subprocess, "Popen", fake_popen)
    healthd._start_capture("b1_mic", "stereo-diagnostic", channels=2)
    assert "--channels=2" in captured["command"]
    assert "--device=b1_mic" in captured["command"]
    assert captured["kwargs"]["stderr"] is healthd.subprocess.DEVNULL


def test_capture_command_rejects_invalid_channel_count() -> None:
    import pytest

    with pytest.raises(ValueError, match="mono or stereo"):
        healthd._start_capture("b1_mic", "invalid", channels=3)


def test_stereo_capture_buffer_drain_preserves_partial_frame_tail() -> None:
    raw = healthd.np.asarray(
        [[0.25, -0.25], [0.5, -0.5], [0.75, -0.75]], dtype=healthd.np.float32
    ).tobytes()
    buffer = bytearray(raw + b"\x00\x00\x00\x00")
    blocks = healthd._drain_stereo_blocks(buffer, 3)
    assert len(blocks) == 1
    assert blocks[0].shape == (3, 2)
    assert blocks[0][2, 0] == 0.75
    assert len(buffer) == 4


def _run_capture_sample_with_fake_selector(monkeypatch, fail_stereo: bool = False):
    clock = [0.0]
    calls: list[tuple[str, int]] = []
    processes = []

    class FakeStream:
        pass

    class FakeProcess:
        def __init__(self):
            self.stdout = FakeStream()
            self.terminated = False
            self.waited = False

        def poll(self):
            return None

        def terminate(self):
            self.terminated = True

        def wait(self, timeout=None):
            self.waited = True

    class FakeSelector:
        def register(self, fileobj, events, data=None):
            return None

        def unregister(self, fileobj):
            return None

        def select(self, timeout=None):
            clock[0] += max(float(timeout or 0.0), 0.01)
            return []

        def close(self):
            return None

    def fake_start_capture(device, stream_name, *, channels=1):
        calls.append((device, channels))
        if fail_stereo and channels == 2:
            raise OSError("mock stereo stream unavailable")
        process = FakeProcess()
        processes.append(process)
        return process

    monkeypatch.setattr(healthd, "_b1_expected_active", lambda: True)
    monkeypatch.setattr(healthd, "_start_capture", fake_start_capture)
    monkeypatch.setattr(healthd.selectors, "DefaultSelector", FakeSelector)
    monkeypatch.setattr(
        healthd.time, "monotonic", lambda: clock.__setitem__(0, clock[0] + 0.01) or clock[0]
    )
    monkeypatch.setattr(healthd.time, "time", lambda: 1000.0)
    summary = healthd.capture_sample(3.0)
    return summary, calls, processes


def test_capture_sample_optional_stereo_tap_is_separate_and_bounded(monkeypatch) -> None:
    summary, calls, processes = _run_capture_sample_with_fake_selector(monkeypatch)
    assert calls == [("sm7b_mono", 1), ("b1_mic", 1), ("b1_mic", 2)]
    assert summary["capture_error"] is None
    assert summary["stereo_channel_metrics"] is None
    assert summary["stereo_capture_error"] == "stereo tap produced no complete frames"
    assert all(process.terminated and process.waited for process in processes)


def test_capture_sample_stereo_tap_failure_does_not_fail_mono_path(monkeypatch) -> None:
    summary, calls, processes = _run_capture_sample_with_fake_selector(
        monkeypatch, fail_stereo=True
    )
    assert calls == [("sm7b_mono", 1), ("b1_mic", 1), ("b1_mic", 2)]
    assert summary["capture_error"] is None
    assert summary["stereo_channel_metrics"] is None
    assert "mock stereo stream unavailable" in summary["stereo_capture_error"]
    assert len(processes) == 2
    assert all(process.terminated and process.waited for process in processes)


def test_capture_sample_accumulates_stereo_channels_without_pcm_persistence(monkeypatch) -> None:
    import tempfile
    from contextlib import ExitStack, contextmanager

    import pytest

    np = pytest.importorskip("numpy")

    rate = 48_000
    frames = healthd.BLOCK_FRAMES
    index = np.arange(frames, dtype=np.float32)
    tone = 0.2 * np.sin(2 * np.pi * 440 * index / rate).astype(np.float32)
    stereo_pcm = np.column_stack((tone, tone)).astype(np.float32).tobytes()
    mono_pcm = tone.tobytes()
    clock = [0.0]
    processes = []
    temporary_files = ExitStack()

    class FakeKey:
        def __init__(self, fileobj, data):
            self.fileobj = fileobj
            self.data = data

    @contextmanager
    def capture_file(data):
        with tempfile.TemporaryFile(mode="w+b") as stream:
            stream.write(data)
            stream.seek(0)
            yield stream

    class FakeProcess:
        def __init__(self, device, channels):
            self._data = stereo_pcm if channels == 2 else mono_pcm
            self.stdout = temporary_files.enter_context(capture_file(self._data))

        def poll(self):
            return None

        def terminate(self):
            return None

        def wait(self, timeout=None):
            return 0

    class FakeSelector:
        def __init__(self):
            self.keys = []
            self.first = True

        def register(self, fileobj, events, data=None):
            self.keys.append(FakeKey(fileobj, data))

        def unregister(self, fileobj):
            self.keys = [key for key in self.keys if key.fileobj is not fileobj]

        def select(self, timeout=None):
            clock[0] += 0.1
            if self.first:
                self.first = False
                return [(key, 1) for key in self.keys]
            return []

        def close(self):
            pass

    def fake_start_capture(device, stream_name, *, channels=1):
        process = FakeProcess(device, channels)
        processes.append(process)
        return process

    monkeypatch.setattr(healthd, "_b1_expected_active", lambda: True)
    monkeypatch.setattr(healthd, "_start_capture", fake_start_capture)
    monkeypatch.setattr(healthd.selectors, "DefaultSelector", FakeSelector)
    monkeypatch.setattr(
        healthd.time, "monotonic", lambda: clock.__setitem__(0, clock[0] + 0.01) or clock[0]
    )
    monkeypatch.setattr(healthd.time, "time", lambda: 1000.0)
    monkeypatch.setattr(healthd, "analyze_signal_features", lambda *args, **kwargs: {})
    try:
        summary = healthd.capture_sample(3.0)
    finally:
        temporary_files.close()

    stereo = summary["stereo_channel_metrics"]
    assert stereo is not None
    assert stereo["frames"] == frames
    assert stereo["correlation"] == 1.0
    assert stereo["phase_opposition_candidate"] is False
    assert summary["stereo_capture_error"] is None
    assert summary["capture_coverage"]["fraction"] < 0.9
    stereo_stream = summary["stream_diagnostics"]["processed_stereo"]
    assert stereo_stream["frames_received"] == frames
    assert stereo_stream["frame_bytes"] == 8
    assert summary["stream_diagnostics"]["raw"]["frames_received"] == frames
    assert summary["stream_diagnostics"]["processed"]["frames_received"] == frames
    assert all(process.stdout.closed for process in processes)


def _baseline_capture_sample(
    raw_rms: float = -70.0,
    raw_high: float = -80.0,
    processed_rms: float = -75.0,
    processed_high: float = -85.0,
) -> dict:
    def low(rms, high):
        return {
            "noise_floor_valid": True,
            "rms_dbfs": rms,
            "5k_12k_dbfs": high,
            "5k_12k_flatness": 0.2,
            "frames": 24,
        }

    return {
        "capture_error": None,
        "capture_coverage": {"fraction": 0.98},
        "fault_confirmed": False,
        "verdict": "NO_SAMPLE",
        "reason": "quiet test fixture",
        "windows": 24,
        "active_windows": 0,
        "valid_windows": 24,
        "candidate_windows": 0,
        "dropout_windows": 0,
        "raw_rms_p50": 0.0,
        "raw_rms_p90": 0.0,
        "post_rms_p50": 0.0,
        "post_rms_p90": 0.0,
        "correlation_p50": 0.0,
        "signal_features": {
            "raw": {"low_energy": low(raw_rms, raw_high), "persistent_tones": []},
            "processed": {
                "low_energy": low(processed_rms, processed_high),
                "persistent_tones": [],
            },
            "raw_to_processed": {},
            "integrity": {"raw": {}, "processed": {}},
        },
        "stream_diagnostics": {},
    }


def test_baseline_enrollment_captures_three_samples_and_saves_numeric_profile(
    monkeypatch, tmp_path
) -> None:
    samples = [_baseline_capture_sample(), _baseline_capture_sample(), _baseline_capture_sample()]
    calls = []
    saved = {}
    monkeypatch.setattr(
        healthd, "capture_sample", lambda seconds: calls.append(seconds) or samples.pop(0)
    )
    monkeypatch.setattr(healthd, "NOISE_BASELINE_PATH", tmp_path / "profile.json")
    monkeypatch.setattr(
        healthd,
        "save_noise_baseline",
        lambda path, profile: saved.update({"path": path, "profile": profile}),
    )
    monkeypatch.setattr(healthd.time, "time", lambda: 100.0)
    assert healthd.enroll_noise_baseline() == 0
    assert calls == [8.0, 8.0, 8.0]
    assert saved["path"] == tmp_path / "profile.json"
    assert saved["profile"]["sample_count"] == 3
    assert saved["profile"]["user_confirmed_quiet"] is True
    assert saved["profile"]["pcm_saved"] is False


def test_baseline_enrollment_refuses_unverified_floor_and_does_not_save(
    monkeypatch, tmp_path
) -> None:
    sample = _baseline_capture_sample()
    sample["signal_features"]["processed"]["low_energy"]["noise_floor_valid"] = False
    saved = []
    monkeypatch.setattr(healthd, "capture_sample", lambda seconds: sample)
    monkeypatch.setattr(healthd, "NOISE_BASELINE_PATH", tmp_path / "profile.json")
    monkeypatch.setattr(healthd, "save_noise_baseline", lambda *args: saved.append(args))
    assert healthd.enroll_noise_baseline() == 2
    assert saved == []


def test_cli_requires_explicit_quiet_confirmation_for_baseline_enrollment(monkeypatch) -> None:
    import pytest

    monkeypatch.setattr(healthd.sys, "argv", ["healthd", "--noise-baseline-enroll"])
    with pytest.raises(SystemExit) as exc:
        healthd.main()
    assert exc.value.code == 2


def test_cli_show_baseline_is_read_only_and_reports_missing(monkeypatch, capsys) -> None:
    monkeypatch.setattr(healthd.sys, "argv", ["healthd", "--noise-baseline-show"])
    monkeypatch.setattr(healthd, "load_noise_baseline", lambda path: None)
    assert healthd.main() == 0
    assert '"status": "baseline_missing"' in capsys.readouterr().out


def test_sample_cycle_logs_noise_baseline_candidate_without_recovery(monkeypatch) -> None:
    baseline_sample = _baseline_capture_sample()
    profile = healthd.build_noise_baseline(
        [baseline_sample, baseline_sample, baseline_sample],
        created_at=1.0,
        confirmed_quiet=True,
    )
    current = _baseline_capture_sample(-60.0, -68.0, -65.0, -73.0)
    current["verdict"] = "CORRECT"
    current["transport"] = {
        "during_sample": {"status": "ok", "node_deltas": {}, "configuration_changes": []},
        "since_previous_sample": {"status": "ok", "node_deltas": {}},
    }
    saved = {}
    recorded = []
    monkeypatch.setattr(healthd, "_read_state", lambda: {})
    monkeypatch.setattr(
        healthd, "capture_sample_with_transport", lambda seconds, previous: (current, {})
    )
    monkeypatch.setattr(healthd, "load_noise_baseline", lambda path: profile)
    monkeypatch.setattr(healthd, "_write_state", lambda state: saved.update(state))
    monkeypatch.setattr(healthd, "_record_sample", lambda sample: recorded.append(sample))
    monkeypatch.setattr(
        healthd,
        "handle_confirmed_fault",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("noise candidate must not restart Carla")
        ),
    )
    monkeypatch.setattr(
        healthd,
        "notify",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("noise candidate must not notify")
        ),
    )
    assert healthd.run_sample_cycle(8.0, {"AUTO_RECOVER": True, "NOTIFY": True}) == 0
    comparison = saved["last_sample"]["noise_baseline_comparison"]
    assert comparison["status"] == "compared"
    assert comparison["candidate"] is True
    assert comparison["noise_rise_candidates"]
    assert saved["noise_baseline_comparison"] == comparison
    assert saved["last_verdict"] == "CORRECT"
    assert recorded and recorded[0]["noise_baseline_comparison"] == comparison


def test_sample_cycle_without_baseline_records_missing_status_and_no_action(monkeypatch) -> None:
    sample = _baseline_capture_sample()
    sample["transport"] = {
        "during_sample": {"status": "ok", "node_deltas": {}, "configuration_changes": []},
        "since_previous_sample": {"status": "ok", "node_deltas": {}},
    }
    saved = {}
    monkeypatch.setattr(healthd, "_read_state", lambda: {})
    monkeypatch.setattr(
        healthd, "capture_sample_with_transport", lambda seconds, previous: (sample, {})
    )
    monkeypatch.setattr(healthd, "load_noise_baseline", lambda path: None)
    monkeypatch.setattr(healthd, "_write_state", lambda state: saved.update(state))
    monkeypatch.setattr(healthd, "_record_sample", lambda sample: None)
    monkeypatch.setattr(
        healthd,
        "handle_confirmed_fault",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("missing profile must not restart")
        ),
    )
    assert healthd.run_sample_cycle(8.0, {"AUTO_RECOVER": True, "NOTIFY": False}) == 0
    comparison = saved["last_sample"]["noise_baseline_comparison"]
    assert comparison["status"] == "baseline_missing"
    assert comparison["candidate"] is False
    assert comparison["automatic_action"] == "none"


def test_cli_rejects_baseline_mode_combined_with_check(monkeypatch) -> None:
    import pytest

    monkeypatch.setattr(
        healthd.sys,
        "argv",
        ["healthd", "--noise-baseline-show", "--check"],
    )
    with pytest.raises(SystemExit) as exc:
        healthd.main()
    assert exc.value.code == 2


@pytest.mark.parametrize("route", ["none", "", "invalid", "astro", "both"])
def test_b1_health_capture_gate_refuses_unrouted_mic(monkeypatch, tmp_path, route) -> None:
    config = tmp_path / "router.conf"
    config.write_text(
        f'B1_ROUTE="{route}"' + chr(10) + 'B1_ACTIVE="true"' + chr(10) + 'CARLA_TO_B1="true"',
        encoding="utf-8",
    )
    monkeypatch.setattr(healthd, "ROUTER_CONFIG_PATH", config)
    monkeypatch.setattr(healthd, "_B1_ACTIVE_CACHE", (0.0, False))
    monkeypatch.setattr(
        healthd,
        "_run_checked",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, "Mute: no", ""),
    )
    assert healthd._b1_expected_active() is False


@pytest.mark.parametrize("route", ["sm7b"])
def test_b1_health_capture_gate_accepts_selected_mic_routes(monkeypatch, tmp_path, route) -> None:
    config = tmp_path / "router.conf"
    config.write_text(
        f'B1_ROUTE="{route}"' + chr(10) + 'B1_ACTIVE="true"' + chr(10) + 'CARLA_TO_B1="true"',
        encoding="utf-8",
    )
    monkeypatch.setattr(healthd, "ROUTER_CONFIG_PATH", config)
    monkeypatch.setattr(healthd, "_B1_ACTIVE_CACHE", (0.0, False))
    monkeypatch.setattr(
        healthd,
        "_run_checked",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, "Mute: no", ""),
    )
    assert healthd._b1_expected_active() is True
