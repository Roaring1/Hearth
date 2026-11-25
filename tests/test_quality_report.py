import json
import math
import struct
import wave

from hearth.audio.quality_report import analyze_wav, main


def write_tone(path, frequency=98.0, frames=48000, rate=48000):
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(rate)
        samples = [
            int(0.1 * 32767 * math.sin(2 * math.pi * frequency * i / rate)) for i in range(frames)
        ]
        output.writeframes(b"".join(struct.pack("<h", sample) for sample in samples))


def test_analyze_wav_reports_unidentified_tone_without_modifying_input(tmp_path):
    source = tmp_path / "tone.wav"
    write_tone(source)
    before = source.read_bytes()
    report = analyze_wav(source)
    assert report["state"] == "healthy_observed"
    assert report["unidentified_tones"][0]["classification"] == "UNIDENTIFIED_TONE"
    assert abs(report["unidentified_tones"][0]["frequency_hz"] - 98.0) < 1.0
    assert source.read_bytes() == before


def test_cli_emits_json_report(tmp_path, capsys):
    source = tmp_path / "tone.wav"
    write_tone(source, frequency=440.0, frames=24000)
    assert main([str(source)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["format"] == "pcm_wav"
    assert report["sample_rate"] == 48000
    assert isinstance(report["unidentified_tones"], list)


def test_cli_fails_closed_for_missing_file(tmp_path, capsys):
    assert main([str(tmp_path / "missing.wav")]) == 2
    assert json.loads(capsys.readouterr().out)["state"] == "inconclusive"
