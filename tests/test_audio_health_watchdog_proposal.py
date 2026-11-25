from __future__ import annotations

import sys
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from audio_health_watchdog_proposal import transport_probe, watchdog_report


def synthetic_wav(path: Path) -> Path:
    import math
    import struct

    rate = 48000
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(
            b"".join(
                struct.pack("<h", int(1200 * math.sin(2 * math.pi * 440 * n / rate)))
                for n in range(rate)
            )
        )
    return path


class FakeProcess:
    def __init__(self, code=0, stdout=""):
        self.returncode = code
        self.stdout = stdout


def test_combined_report_offline_with_injected_transport(tmp_path):
    wav = synthetic_wav(tmp_path / "synthetic.wav")
    report = watchdog_report(
        wav, lambda: {"pw_top_ok": True, "metadata_ok": True, "nodes": {}, "errors": []}
    )
    assert report["decision"] == "measurement_only"
    assert report["automatic_action"] == "none"
    assert report["signal"]["state"] in {"healthy_observed", "inconclusive", "no_sample"}
    assert report["transport"]["metadata_ok"] is True
    assert report["confidence_states"] == {
        "signal": "low",
        "transport": "unknown",
        "overall": "low",
    }


def test_transport_probe_success_parses_fake_pwtop_and_metadata(monkeypatch):
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        if argv[0] == "pw-top":
            return FakeProcess(0, "")
        return FakeProcess(0, "settings fixture")

    snapshot = transport_probe(fake_run)
    assert snapshot["pw_top_ok"] and snapshot["metadata_ok"]
    assert calls == [["pw-top", "-b", "-n", "6"], ["pw-metadata", "-n", "settings"]]


def test_transport_probe_failures_are_explicit():
    def failed(argv, **kwargs):
        return FakeProcess(9, "")

    result = transport_probe(failed)
    assert not result["pw_top_ok"] and not result["metadata_ok"]
    assert len(result["errors"]) == 2
