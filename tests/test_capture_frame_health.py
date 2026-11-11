"""The mpv frame-health probe is active-session-only and exposes no pixel data."""

from __future__ import annotations

import os
import shutil
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
    assert 'mp.observe_property("user-data/hearth/no-signal", "bool"' in FRAME_HEALTH_LUA
    assert 'mp.create_osd_overlay("ass-events")' in FRAME_HEALTH_LUA
    assert "Video stopped updating" in FRAME_HEALTH_LUA
    assert r"\\an5\\pos(%d,%d)" in FRAME_HEALTH_LUA
    assert "No signal" not in FRAME_HEALTH_LUA
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


@pytest.mark.skipif(
    not os.environ.get("DISPLAY") or shutil.which("mpv") is None or shutil.which("ffmpeg") is None,
    reason="requires mpv, ffmpeg and an isolated X display",
)
def test_no_signal_overlay_renders_and_clears_in_real_mpv(tmp_path: Path) -> None:
    """Exercise the actual ASS overlay, not just the generated Lua source."""
    import json
    import socket
    import subprocess
    import time

    runtime = tmp_path / "runtime"
    runtime.mkdir(mode=0o700)
    runtime.chmod(0o700)
    video = tmp_path / "pattern.mkv"
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=0x202020:s=320x180:r=30",
            "-t",
            "8",
            "-c:v",
            "mpeg4",
            "-pix_fmt",
            "yuv420p",
            "-y",
            str(video),
        ],
        check=True,
        timeout=8,
    )
    script = write_frame_health_script(runtime)
    socket_path = runtime / "mpv.sock"
    env = os.environ.copy()
    env["LIBGL_ALWAYS_SOFTWARE"] = "1"
    process = subprocess.Popen(
        [
            "mpv",
            "--no-config",
            "--no-terminal",
            "--vo=gpu",
            "--osd-level=0",
            "--no-osc",
            "--no-osd-bar",
            "--no-audio",
            "--no-sub",
            "--no-input-default-bindings",
            "--force-window=yes",
            "--idle=yes",
            "--keep-open=yes",
            "--geometry=1280x720+0+0",
            "--autofit=1280x720",
            f"--input-ipc-server={socket_path}",
            f"--script={script}",
            str(video),
        ],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    connection: socket.socket | None = None
    try:
        deadline = time.monotonic() + 4.0
        while time.monotonic() < deadline and not socket_path.exists():
            if process.poll() is not None:
                pytest.fail("mpv exited before creating its IPC socket")
            time.sleep(0.02)
        assert socket_path.exists(), "mpv IPC socket was not created"
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        connection.settimeout(2.0)
        connection.connect(str(socket_path))
        ipc = connection
        next_id = 1
        receive_buffer = bytearray()

        def request(command: list[object]) -> dict[str, object]:
            nonlocal next_id, receive_buffer
            request_id = next_id
            next_id += 1
            ipc.sendall(
                (json.dumps({"command": command, "request_id": request_id}) + "\n").encode()
            )
            while True:
                while b"\n" not in receive_buffer:
                    chunk = ipc.recv(4096)
                    if not chunk:
                        raise AssertionError("mpv IPC closed before replying")
                    receive_buffer.extend(chunk)
                line, _, rest = receive_buffer.partition(b"\n")
                receive_buffer = bytearray(rest)
                message = json.loads(line)
                if isinstance(message, dict) and message.get("request_id") == request_id:
                    return message

        frame_deadline = time.monotonic() + 4.0
        while time.monotonic() < frame_deadline:
            response = request(["get_property", "time-pos"])
            position = response.get("data")
            if (
                response.get("error") == "success"
                and isinstance(position, (int, float))
                and position > 0.2
            ):
                break
            time.sleep(0.05)
        else:
            pytest.fail("mpv did not begin advancing time-pos")

        def capture_center_white_pixels() -> int:
            raw = subprocess.run(
                [
                    "ffmpeg",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-f",
                    "x11grab",
                    "-video_size",
                    "1280x720",
                    "-i",
                    os.environ["DISPLAY"],
                    "-frames:v",
                    "1",
                    "-f",
                    "rawvideo",
                    "-pix_fmt",
                    "rgb24",
                    "pipe:1",
                ],
                check=True,
                capture_output=True,
                timeout=5,
            ).stdout
            assert len(raw) == 1280 * 720 * 3
            count = 0
            for y in range(300, 420):
                for x in range(450, 830):
                    offset = (y * 1280 + x) * 3
                    red, green, blue = raw[offset : offset + 3]
                    if red > 230 and green > 230 and blue > 230:
                        count += 1
            return count

        assert (
            request(["set_property", "user-data/hearth/no-signal", True]).get("error") == "success"
        )
        time.sleep(0.35)
        with_signal = capture_center_white_pixels()
        assert (
            request(["set_property", "user-data/hearth/no-signal", False]).get("error") == "success"
        )
        time.sleep(0.35)
        without_signal = capture_center_white_pixels()
        assert with_signal > without_signal + 500
    finally:
        if connection is not None:
            connection.close()
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2.0)
