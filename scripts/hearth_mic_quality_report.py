#!/usr/bin/env python3
"""Passive mic-quality reporter: 10s capture, offline analysis, bounded retention."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

OUT = Path.home() / ".local/state/hearth/quality"
SRC = Path("/home/roaring/Hearth-integrated-r3-20261125/src")
sys.path.insert(0, str(SRC))
from hearth.audio.quality_report import analyze_wav  # noqa: E402


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    wav = OUT / f"{stamp}.wav"
    report = OUT / f"{stamp}.json"
    try:
        command = [
            "timeout",
            "--signal=INT",
            "10s",
            "pw-record",
            "--target",
            "b1_mic",
            "--rate",
            "48000",
            "--channels",
            "2",
            str(wav),
        ]
        capture = subprocess.run(command, timeout=15, check=False)
        if capture.returncode not in (0, 124):
            raise RuntimeError(f"pw-record/timeout exit={capture.returncode}")
        result = analyze_wav(wav)
        result.update(
            {
                "capture_target": "b1_mic",
                "capture_seconds_requested": 10,
                "decision": "measurement_only",
                "automatic_action": "none",
            }
        )
        report.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    except Exception as exc:
        error = {
            "state": "capture_or_analysis_error",
            "error": str(exc),
            "decision": "measurement_only",
            "automatic_action": "none",
        }
        report.write_text(json.dumps(error, indent=2) + "\n", encoding="utf-8")
        raise
    files = sorted(OUT.glob("*.wav"), reverse=True)
    for old in files[48:]:
        old.unlink(missing_ok=True)
        (OUT / f"{old.stem}.json").unlink(missing_ok=True)
    print(report)


if __name__ == "__main__":
    main()
