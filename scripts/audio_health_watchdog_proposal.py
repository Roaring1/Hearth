#!/usr/bin/env python3
"""Non-installable proposal: offline signal report plus optional read-only transport probe."

This is a design spike only. It never opens microphone taps, persists state, or repairs routes.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
from audio_health_transport import parse_pw_top  # noqa: E402

sys.path.insert(0, str(SCRIPT_DIR.parent / "src"))
from hearth.audio.quality_report import analyze_wav  # noqa: E402


def transport_probe(run: Callable[..., Any] = subprocess.run) -> dict[str, Any]:
    """Read PipeWire status with bounded commands; probe failures are explicit."""
    result: dict[str, Any] = {"pw_top_ok": False, "nodes": {}, "errors": []}
    try:
        top = run(
            ["pw-top", "-b", "-n", "6"], capture_output=True, text=True, timeout=8, check=False
        )
        if top.returncode == 0:
            result["nodes"] = parse_pw_top(top.stdout)
            result["pw_top_ok"] = True
        else:
            result["errors"].append(f"pw-top exit={top.returncode}")
    except (OSError, subprocess.TimeoutExpired) as exc:
        result["errors"].append(f"pw-top unavailable: {type(exc).__name__}")
    try:
        metadata = run(
            ["pw-metadata", "-n", "settings"],
            capture_output=True,
            text=True,
            timeout=4,
            check=False,
        )
        result["metadata_ok"] = metadata.returncode == 0
        if metadata.returncode != 0:
            result["errors"].append(f"pw-metadata exit={metadata.returncode}")
    except (OSError, subprocess.TimeoutExpired) as exc:
        result["metadata_ok"] = False
        result["errors"].append(f"pw-metadata unavailable: {type(exc).__name__}")
    return result


def watchdog_report(
    wav_path: Path, probe: Callable[[], dict[str, Any]] = transport_probe
) -> dict[str, Any]:
    """Combine offline analyzer state with transport observability, never actions."""
    signal = analyze_wav(wav_path)
    transport = probe()
    return {
        "schema": "hearth-watchdog-proposal-v1",
        "signal": signal,
        "transport": transport,
        "decision": "measurement_only",
        "automatic_action": "none",
        "confidence_states": {
            "signal": "low" if signal.get("metrics") is not None else "unknown",
            "transport": "unknown",
            "overall": "low" if signal.get("metrics") is not None else "unknown",
        },
        "limitations": [
            "WAV analysis is offline, not a microphone capture.",
            "Transport probe is observational only.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wav", type=Path, help="Uncompressed PCM WAV file for offline analysis")
    args = parser.parse_args()
    try:
        output = watchdog_report(args.wav)
    except (OSError, ValueError, EOFError) as exc:
        print(json.dumps({"state": "inconclusive", "error": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps(output, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
