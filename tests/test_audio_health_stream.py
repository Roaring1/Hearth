"""Tests for bounded scheduled PCM stream diagnostics."""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from audio_health_stream import StreamReadDiagnostics  # noqa: E402


def test_stream_read_cadence_and_overflow_are_recorded_per_source() -> None:
    monitor = StreamReadDiagnostics(("raw", "processed"))
    monitor.observe_read("raw", 4096, 1.0)
    monitor.observe_read("raw", 4096, 1.1)
    monitor.observe_read("raw", 4096, 1.5)
    monitor.observe_read("processed", 4096, 1.0)
    monitor.observe_read("processed", 4096, 1.15)
    monitor.record_buffer_overflow("raw", 4800 * 4)

    result = monitor.summary(48_000)
    assert result["raw"]["read_calls"] == 3
    assert result["raw"]["frames_received"] == 3072
    assert result["raw"]["max_inter_read_gap_ms"] == 400.0
    assert result["raw"]["read_gaps_over_250ms"] == 1
    assert result["raw"]["buffer_overflow_frames"] == 4800
    assert result["processed"]["read_gaps_over_250ms"] == 0
    assert result["automatic_action"] == "none"


def test_exact_repeated_aligned_blocks_are_counted_without_retaining_pcm() -> None:
    monitor = StreamReadDiagnostics(("raw", "processed"))
    raw = bytes(range(256)) * 16
    processed = b"processed" * 400
    monitor.record_aligned_window(raw, processed)
    monitor.record_aligned_window(raw, processed)
    monitor.record_aligned_window(raw, processed)

    result = monitor.summary(48_000)
    assert result["paired_windows"] == 3
    assert result["identical_adjacent_windows"] == {
        "raw": 2,
        "processed": 2,
        "both": 2,
    }
    assert result["max_identical_window_streak"] == {
        "raw": 2,
        "processed": 2,
        "both": 2,
    }
    assert "not a standalone" in result["interpretation"]
    assert result["automatic_action"] == "none"


def test_nonidentical_audio_windows_reset_repeat_streaks() -> None:
    monitor = StreamReadDiagnostics(("raw", "processed"))
    monitor.record_aligned_window(b"a" * 1024, b"x" * 1024)
    monitor.record_aligned_window(b"a" * 1024, b"y" * 1024)
    monitor.record_aligned_window(b"b" * 1024, b"z" * 1024)

    result = monitor.summary(48_000)
    assert result["identical_adjacent_windows"]["raw"] == 1
    assert result["identical_adjacent_windows"]["processed"] == 0
    assert result["identical_adjacent_windows"]["both"] == 0
    assert result["max_identical_window_streak"]["both"] == 0


def test_no_read_or_unknown_device_does_not_create_fake_data() -> None:
    monitor = StreamReadDiagnostics(("raw", "processed"))
    monitor.observe_read("other", 4096, 1.0)
    monitor.observe_read("raw", 0, 1.1)
    result = monitor.summary(48_000)
    assert result["raw"]["read_calls"] == 0
    assert result["raw"]["frames_received"] == 0


def test_summary_reports_pcm_format_and_rejects_invalid_sample_width() -> None:
    import pytest

    monitor = StreamReadDiagnostics(("raw", "processed"))
    result = monitor.summary(48_000, 4)
    assert result["sample_rate"] == 48_000
    assert result["sample_bytes"] == 4
    with pytest.raises(ValueError, match="sample_bytes"):
        monitor.summary(48_000, 0)
