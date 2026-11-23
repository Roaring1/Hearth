"""Tests for cumulative PipeWire transport health accounting."""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from audio_health_transport import (  # noqa: E402
    compare_snapshots,
    parse_pw_top,
    snapshot_for_state,
)


def _snapshot(err: int, *, node_id: int = 10, at: float = 1.0) -> dict:
    return {
        "sampled_at_monotonic": at,
        "pw_top_ok": True,
        "metadata_ok": True,
        "quantum": 512,
        "rate": 48_000,
        "nodes": {
            "scarlett": {
                "id": node_id,
                "name": "scarlett",
                "status": "R",
                "errors": err,
                "quantum": 512,
                "rate": 48_000,
                "wait": "0.02",
                "busy": "0.10",
                "wq": "0.04",
                "bq": "0.20",
            }
        },
    }


def test_parse_pw_top_captures_transport_and_load_columns() -> None:
    text = (
        "S ID QUANT RATE WAIT BUSY W/Q B/Q ERR NAME\n"
        "R 42 512 48000 0.02 0.10 0.04 0.20 7 "
        "alsa_input.usb-Focusrite_Scarlett_Solo\n"
        "R 43 512 48000 0.03 0.11 0.06 0.22 2 Carla\n"
        "R 44 512 48000 0.01 0.08 0.02 0.16 1 input.b1_mic\n"
    )
    parsed = parse_pw_top(text)
    assert set(parsed) == {"scarlett", "carla", "b1"}
    assert parsed["scarlett"]["id"] == 42
    assert parsed["scarlett"]["errors"] == 7
    assert parsed["scarlett"]["wait"] == "0.02"
    assert parsed["scarlett"]["busy"] == "0.10"
    assert parsed["scarlett"]["wq"] == "0.04"
    assert parsed["scarlett"]["bq"] == "0.20"
    assert parsed["b1"]["name"] == "input.b1_mic"


def test_same_node_counter_increment_is_reported() -> None:
    result = compare_snapshots(_snapshot(8, at=1.0), _snapshot(11, at=9.0))
    assert result["status"] == "available"
    assert result["node_deltas"] == {"scarlett": 3}
    assert result["elapsed_seconds"] == 8.0
    assert result["identity_changes"] == []
    assert result["counter_resets"] == []


def test_first_observation_is_baseline_not_zero_errors() -> None:
    result = compare_snapshots(None, _snapshot(900))
    assert result["status"] == "baseline"
    assert result["node_deltas"] == {}


def test_node_recreation_does_not_create_a_false_error_delta() -> None:
    result = compare_snapshots(
        _snapshot(50, node_id=10),
        _snapshot(3, node_id=99, at=8.0),
    )
    assert result["status"] == "partial"
    assert result["node_deltas"] == {}
    assert result["identity_changes"]


def test_counter_decrease_is_treated_as_reset_not_negative_or_new_xrun() -> None:
    result = compare_snapshots(_snapshot(50), _snapshot(0, at=8.0))
    assert result["status"] == "partial"
    assert result["node_deltas"] == {}
    assert result["counter_resets"]


def test_failed_query_is_never_reported_as_zero_errors() -> None:
    before = _snapshot(50)
    after = _snapshot(50, at=8.0)
    after["pw_top_ok"] = False
    result = compare_snapshots(before, after)
    assert result["status"] == "partial"
    assert result["node_deltas"] == {}


def test_quantum_and_rate_changes_are_reported() -> None:
    before = _snapshot(1)
    after = _snapshot(1, at=8.0)
    after["quantum"] = 1024
    after["rate"] = 44_100
    after["nodes"]["scarlett"]["quantum"] = 1024
    after["nodes"]["scarlett"]["rate"] = 44_100
    result = compare_snapshots(before, after)
    assert result["status"] == "available"
    assert len(result["configuration_changes"]) == 4


def test_persisted_snapshot_is_bounded_and_contains_no_pcm() -> None:
    source = _snapshot(4)
    source["untrusted_payload"] = "not retained"
    source["nodes"]["scarlett"]["pcm"] = b"audio"
    result = snapshot_for_state(source)
    assert result["nodes"]["scarlett"]["errors"] == 4
    assert "untrusted_payload" not in result
    assert "pcm" not in result["nodes"]["scarlett"]


def test_metadata_failure_keeps_counter_result_partial() -> None:
    before = _snapshot(5)
    after = _snapshot(7, at=8.0)
    after["metadata_ok"] = False
    result = compare_snapshots(before, after)
    assert result["status"] == "partial"
    assert result["node_deltas"] == {"scarlett": 2}


def test_parser_accepts_live_pw_top_rows_with_formats_and_pipewire_names() -> None:
    rows = [
        "S ID QUANT RATE WAIT BUSY W/Q B/Q ERR FORMAT NAME",
        (
            "R 94 512 48000 825.3us 0.8us 0.08 0.00 0 "
            "S32LE 2 48000 "
            "alsa_input.usb-Focusrite_Scarlett_Solo_USB_direct_source"
        ),
        "R 267 0 0 1.2us 2.3us 0.00 0.00 0 F32P 2 0 + b1_mic",
        "R 268 0 0 3.2us 3.3us 0.00 0.00 0 F32P 2 0 + input.b1_mic",
        "R 304 512 48000 9.0us 766.3us 0.00 0.07 0 + Carla",
    ]
    parsed = parse_pw_top(chr(10).join(rows))
    assert parsed["scarlett"]["id"] == 94
    assert parsed["carla"]["id"] == 304
    assert parsed["b1"]["id"] == 268
    assert parsed["b1"]["name"] == "input.b1_mic"


def test_parser_keeps_nonzero_quantum_and_rate_across_zero_placeholder_rows() -> None:
    rows = [
        "S ID QUANT RATE WAIT BUSY W/Q B/Q ERR FORMAT NAME",
        "R 304 512 48000 10us 800us 0.01 0.07 2 + Carla",
        "R 304 0 0 11us 0.0us ??? ??? 2 + Carla",
    ]
    parsed = parse_pw_top(chr(10).join(rows))
    assert parsed["carla"]["quantum"] == 512
    assert parsed["carla"]["rate"] == 48_000
    assert parsed["carla"]["errors"] == 2
    assert parsed["carla"]["wait"] == "11us"
    assert parsed["carla"]["wq"] == "0.01"
