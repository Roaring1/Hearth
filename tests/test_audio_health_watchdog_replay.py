from __future__ import annotations

import math
import struct
import wave
from pathlib import Path

import pytest

from hearth.audio.quality_report import analyze_wav
from hearth.audio.signal_quality import DropoutState, SignalState, analyze_pcm, detect_dropouts

RATE = 48_000
DURATION = 1.0
COUNT = int(RATE * DURATION)


def pcm_tone(frequency: float, *, amplitude: float = 0.04) -> list[float]:
    return [amplitude * math.sin(2 * math.pi * frequency * index / RATE) for index in range(COUNT)]


def write_wav(path: Path, samples: list[float]) -> Path:
    encoded = bytearray()
    for sample in samples:
        bounded = max(-1.0, min(1.0, sample))
        integer = round(bounded * 32767)
        encoded.extend(struct.pack("<h", integer))
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(RATE)
        wav.writeframes(encoded)
    return path


@pytest.mark.parametrize("frequency", [50.0, 60.0, 97.0, 98.0, 99.0])
def test_replay_stable_low_tones_remain_unidentified_and_low_confidence(tmp_path, frequency):
    report = analyze_wav(write_wav(tmp_path / f"tone-{frequency}.wav", pcm_tone(frequency)))
    tones = report["unidentified_tones"]
    assert len(tones) == 1
    assert tones[0]["classification"] == "UNIDENTIFIED_TONE"
    assert tones[0]["frequency_hz"] == pytest.approx(frequency, abs=0.2)
    assert tones[0]["confidence"] == "low"
    assert "mains" not in tones[0]["classification"].lower()
    assert "hum" not in tones[0]["classification"].lower()


def test_replay_clipping_is_a_sample_candidate_not_quality_certification(tmp_path):
    report = analyze_wav(write_wav(tmp_path / "clip.wav", pcm_tone(440, amplitude=1.0)))
    assert report["state"] == SignalState.SUSPECT.value
    assert report["metrics"]["clipped_sample_count"] > 0
    assert "clipping candidate samples present" in report["reasons"]


def test_replay_dropout_detects_long_low_rms_gap_only_as_candidate():
    samples = pcm_tone(440, amplitude=0.2)
    gap_start, gap_end = 12_000, 16_000
    samples[gap_start:gap_end] = [0.0] * (gap_end - gap_start)
    report = detect_dropouts(samples, RATE)
    assert report.state is DropoutState.CANDIDATE
    assert report.longest_dropout_windows >= 3
    assert report.dropout_start_samples[0] == pytest.approx(gap_start, abs=report.window_samples)
    assert "not confirmed faults" in report.limitations[0]
    signal_report = analyze_pcm(samples, RATE)
    assert signal_report.state is SignalState.HEALTHY_OBSERVED


def test_replay_silence_is_no_sample_not_suspect(tmp_path):
    report = analyze_wav(write_wav(tmp_path / "silence.wav", [0.0] * COUNT))
    assert report["state"] == SignalState.NO_SAMPLE.value
    assert report["metrics"]["peak"] == 0.0
    assert report["unidentified_tones"] == []
