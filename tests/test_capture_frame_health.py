"""The mpv frame-health probe is active-session-only and exposes no pixel data."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from hearth.capture_frame_health import (
    FRAME_HEALTH_LUA,
    FrameHealthError,
    parse_frame_health_payload,
    write_frame_health_script,
)


def _payload(**changes: object) -> dict[str, object]:
    value: dict[str, object] = {
        "ready": True,
        "state": "changing",
        "width": 1920,
        "height": 1080,
        "sample_count": 12,
        "black_fraction": 0.01,
        "unchanged_s": 0.0,
        "black_s": 0.0,
    }
    value.update(changes)
    return value


def test_lua_probe_is_event_driven_and_never_persists_raw_screenshots() -> None:
    assert 'mp.observe_property("time-pos"' in FRAME_HEALTH_LUA
    assert 'pcall(mp.command_native, {"screenshot-raw", "video", "bgr0"})' in FRAME_HEALTH_LUA
    assert 'mp.set_property_native("user-data/hearth/frame-health"' in FRAME_HEALTH_LUA
    assert "local SAMPLE_INTERVAL = 2.0" in FRAME_HEALTH_LUA
    assert "mp.add_timeout" not in FRAME_HEALTH_LUA
    assert "mp.add_periodic_timer" not in FRAME_HEALTH_LUA
    assert "screenshot-to-file" not in FRAME_HEALTH_LUA
    assert "io.open" not in FRAME_HEALTH_LUA
    assert "os.execute" not in FRAME_HEALTH_LUA
    assert "io.popen" not in FRAME_HEALTH_LUA


def test_frame_health_parser_accepts_only_bounded_content_free_metadata() -> None:
    snapshot = parse_frame_health_payload(
        _payload(state="mostly_black", black_fraction=0.995, black_s=8.0)
    )

    assert snapshot is not None
    assert snapshot.state == "mostly_black"
    assert snapshot.width == 1920
    assert snapshot.black_fraction == 0.995
    assert snapshot.black_s == 8.0


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {"ready": True, "state": "raw_pixels"},
        _payload(width=True),
        _payload(height=-1),
        _payload(sample_count=1.5),
        _payload(black_fraction=1.1),
        _payload(black_fraction=float("nan")),
        _payload(unchanged_s=float("inf")),
        _payload(black_s=-1.0),
        _payload(state="unchanged", width=0),
    ],
)
def test_frame_health_parser_rejects_invalid_or_out_of_range_payloads(payload: object) -> None:
    assert parse_frame_health_payload(payload) is None


def test_script_is_created_private_and_contains_only_the_probe(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    runtime.mkdir(mode=0o700)
    runtime.chmod(0o700)

    path = write_frame_health_script(runtime)

    metadata = path.stat()
    assert metadata.st_uid == os.getuid()
    assert stat.S_IMODE(metadata.st_mode) == 0o600
    assert path.read_text(encoding="utf-8") == FRAME_HEALTH_LUA


def test_script_writer_refuses_non_private_runtime_directory(tmp_path: Path) -> None:
    runtime = tmp_path / "shared-runtime"
    runtime.mkdir(mode=0o755)
    runtime.chmod(0o755)

    with pytest.raises(FrameHealthError, match="not private"):
        write_frame_health_script(runtime)

    assert list(runtime.iterdir()) == []


def test_existing_script_is_never_overwritten_or_removed(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    runtime.mkdir(mode=0o700)
    runtime.chmod(0o700)
    existing = runtime / "frame-health.lua"
    existing.write_text("user-owned marker", encoding="utf-8")
    existing.chmod(0o600)

    with pytest.raises(FrameHealthError, match="File exists"):
        write_frame_health_script(runtime)

    assert existing.read_text(encoding="utf-8") == "user-owned marker"
