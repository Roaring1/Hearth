"""Read-only WAV quality report CLI built on Hearth's pure signal-quality core."""

from __future__ import annotations

import argparse
import json
import sys
import wave
from pathlib import Path
from typing import Any

from hearth.audio.signal_quality import analyze_pcm


def _decode_pcm(raw: bytes, sample_width: int, channels: int) -> list[float]:
    if sample_width not in (1, 2, 3, 4):
        raise ValueError(f"unsupported PCM sample width: {sample_width} bytes")
    frame_width = sample_width * channels
    if not channels or len(raw) % frame_width:
        raise ValueError("WAV data is not aligned to complete PCM frames")
    values: list[float] = []
    for offset in range(0, len(raw), frame_width):
        total = 0.0
        for channel in range(channels):
            start = offset + channel * sample_width
            chunk = raw[start : start + sample_width]
            if sample_width == 1:
                sample = chunk[0] - 128
                scale = 128.0
            else:
                sample = int.from_bytes(chunk, "little", signed=True)
                scale = float(1 << (sample_width * 8 - 1))
            total += sample / scale
        values.append(total / channels)
    return values


def analyze_wav(path: Path) -> dict[str, Any]:
    """Decode bounded uncompressed PCM WAV and return JSON-safe measurements."""
    with wave.open(str(path), "rb") as source:
        if source.getcomptype() != "NONE":
            raise ValueError("only uncompressed PCM WAV files are supported")
        channels = source.getnchannels()
        sample_rate = source.getframerate()
        frame_count = source.getnframes()
        sample_width = source.getsampwidth()
        if frame_count > 480_000:
            raise ValueError("WAV exceeds the 480000-frame analysis bound")
        raw = source.readframes(frame_count)
        if len(raw) != frame_count * channels * sample_width:
            raise ValueError("WAV data is truncated")
    samples = _decode_pcm(raw, sample_width, channels)
    report = analyze_pcm(samples, sample_rate)
    metrics = report.metrics
    tone_list: list[dict[str, Any]] = []
    if metrics is not None and metrics.stable_tone.classification == "UNIDENTIFIED_TONE":
        tone_list.append(
            {
                "classification": "UNIDENTIFIED_TONE",
                "frequency_hz": metrics.stable_tone.frequency_hz,
                "confidence": metrics.stable_tone.confidence,
            }
        )
    return {
        "path": str(path),
        "format": "pcm_wav",
        "channels": channels,
        "sample_rate": sample_rate,
        "sample_count": len(samples),
        "state": report.state.value,
        "reasons": list(report.reasons),
        "limitations": list(report.limitations),
        "metrics": None
        if metrics is None
        else {
            "duration_seconds": metrics.duration_seconds,
            "rms": metrics.rms,
            "peak": metrics.peak,
            "dc_offset": metrics.dc_offset,
            "true_peak_estimate": metrics.true_peak_estimate,
            "clipped_sample_count": metrics.clipped_sample_count,
            "transient_candidates": metrics.transient_candidates,
        },
        "unidentified_tones": tone_list,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Analyze an uncompressed PCM WAV without modifying it"
    )
    parser.add_argument("wav_file", type=Path)
    args = parser.parse_args(argv)
    try:
        result = analyze_wav(args.wav_file)
    except (OSError, EOFError, wave.Error, ValueError) as exc:
        print(json.dumps({"state": "inconclusive", "error": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
