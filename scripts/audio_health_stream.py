"""Bounded metadata about scheduled PCM stream reads and aligned windows.

The monitor retains counters and short cryptographic fingerprints only, never
waveform bytes. Repeated periodic audio can legitimately repeat a block, so
repeat counts are evidence for review, not a fault verdict.
"""

from __future__ import annotations

import hashlib
from typing import Any


class StreamReadDiagnostics:
    """Collect timing, truncation and exact repeated-window counters."""

    def __init__(self, devices: tuple[str, ...]) -> None:
        self._devices = {
            name: {
                "read_calls": 0,
                "bytes_received": 0,
                "max_inter_read_gap_ms": 0.0,
                "read_gaps_over_250ms": 0,
                "buffer_overflow_bytes": 0,
                "unpaired_tail_bytes": 0,
                "last_read_at": None,
                "previous_digest": None,
                "repeat_windows": 0,
                "repeat_streak": 0,
                "max_repeat_streak": 0,
            }
            for name in devices
        }
        self._paired_windows = 0
        self._both_repeat_windows = 0
        self._both_repeat_streak = 0
        self._both_max_repeat_streak = 0

    def observe_read(self, device: str, byte_count: int, now: float) -> None:
        state = self._devices.get(device)
        if state is None or byte_count <= 0:
            return
        state["read_calls"] += 1
        state["bytes_received"] += byte_count
        previous = state["last_read_at"]
        if isinstance(previous, (int, float)) and now >= previous:
            gap_ms = (now - previous) * 1000.0
            state["max_inter_read_gap_ms"] = max(float(state["max_inter_read_gap_ms"]), gap_ms)
            if gap_ms > 250.0:
                state["read_gaps_over_250ms"] += 1
        state["last_read_at"] = now

    def record_buffer_overflow(self, device: str, byte_count: int) -> None:
        state = self._devices.get(device)
        if state is not None and byte_count > 0:
            state["buffer_overflow_bytes"] += byte_count

    def record_unpaired_tail(self, device: str, byte_count: int) -> None:
        """Record bytes still unmatched in one tap when a sample ends."""
        state = self._devices.get(device)
        if state is not None and byte_count > 0:
            state["unpaired_tail_bytes"] += byte_count

    def record_aligned_window(self, raw_bytes: bytes, processed_bytes: bytes) -> None:
        self._paired_windows += 1
        for name, payload in (("raw", raw_bytes), ("processed", processed_bytes)):
            state = self._devices.get(name)
            if state is None:
                continue
            digest = hashlib.blake2s(payload, digest_size=8).digest()
            previous = state["previous_digest"]
            if previous == digest:
                state["repeat_windows"] += 1
                state["repeat_streak"] += 1
                state["max_repeat_streak"] = max(state["max_repeat_streak"], state["repeat_streak"])
            else:
                state["repeat_streak"] = 0
            state["previous_digest"] = digest

        raw_state = self._devices.get("raw")
        processed_state = self._devices.get("processed")
        both_repeat = bool(
            raw_state
            and processed_state
            and raw_state["repeat_streak"] > 0
            and processed_state["repeat_streak"] > 0
        )
        if both_repeat:
            self._both_repeat_windows += 1
            self._both_repeat_streak += 1
            self._both_max_repeat_streak = max(
                self._both_max_repeat_streak, self._both_repeat_streak
            )
        else:
            self._both_repeat_streak = 0

    def summary(self, sample_rate: int, sample_bytes: int = 4) -> dict[str, Any]:
        if sample_bytes < 1:
            raise ValueError("sample_bytes must be at least one byte")
        result: dict[str, Any] = {
            "sample_rate": int(sample_rate),
            "sample_bytes": int(sample_bytes),
            "paired_windows": self._paired_windows,
            "identical_adjacent_windows": {
                "raw": self._devices.get("raw", {}).get("repeat_windows", 0),
                "processed": self._devices.get("processed", {}).get("repeat_windows", 0),
                "both": self._both_repeat_windows,
            },
            "max_identical_window_streak": {
                "raw": self._devices.get("raw", {}).get("max_repeat_streak", 0),
                "processed": self._devices.get("processed", {}).get("max_repeat_streak", 0),
                "both": self._both_max_repeat_streak,
            },
            "interpretation": (
                "metadata_only; identical periodic audio can legitimately repeat; "
                "not a standalone dropout/frozen-stream verdict"
            ),
        }
        for name, state in self._devices.items():
            bytes_received = int(state["bytes_received"])
            result[name] = {
                "read_calls": state["read_calls"],
                "bytes_received": bytes_received,
                "frames_received": bytes_received // sample_bytes,
                "trailing_partial_sample_bytes": bytes_received % sample_bytes,
                "max_inter_read_gap_ms": round(float(state["max_inter_read_gap_ms"]), 3),
                "read_gaps_over_250ms": state["read_gaps_over_250ms"],
                "buffer_overflow_bytes": state["buffer_overflow_bytes"],
                "buffer_overflow_frames": state["buffer_overflow_bytes"] // sample_bytes,
                "unpaired_tail_bytes": state["unpaired_tail_bytes"],
                "unpaired_tail_frames": state["unpaired_tail_bytes"] // sample_bytes,
                "unpaired_tail_partial_sample_bytes": state["unpaired_tail_bytes"] % sample_bytes,
            }
        result["automatic_action"] = "none"
        return result
