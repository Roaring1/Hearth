"""Pure mpv argv tests; never open a video node or start mpv."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pytest

from hearth.capture_mpv_ipc import MpvViewerProcess
from hearth.capture_session import ResolvedCaptureDevice, UsbIdentity, VideoMode
from hearth.capture_viewer import (
    MpvCaptureViewer,
    UnsupportedCaptureFormat,
    build_mpv_viewer_argv,
)


def _device(pixel_format: str = "MJPG") -> ResolvedCaptureDevice:
    return ResolvedCaptureDevice(
        identity=UsbIdentity("1abc", "02ef", serial_number="fixture-card"),
        video_node_path="/dev/video42",
        mode=VideoMode(pixel_format, 1920, 1080, 60_000, 1_001),
    )


def test_mjpeg_viewer_argv_is_video_only_and_pins_discrete_mode() -> None:
    argv = build_mpv_viewer_argv(_device())

    assert argv[0] == "mpv"
    assert "--no-config" in argv
    assert "--title=Hearth - Share Output" in argv
    assert "--audio=no" in argv
    assert "--no-osc" in argv
    assert "--osd-level=0" in argv
    assert "--no-sub" in argv
    assert "--cursor-autohide=always" in argv
    assert "--input-builtin-bindings=no" in argv
    assert not any(arg.startswith("--demuxer=") for arg in argv)
    assert "--demuxer-lavf-format=v4l2" not in argv
    assert argv[-1].startswith("av://v4l2:")
    assert "--demuxer-lavf-o=input_format=mjpeg,video_size=1920x1080,framerate=60000/1001" in argv
    assert argv[-2:] == ["--", "av://v4l2:/dev/video42"]


def test_fullscreen_is_an_explicit_compositor_request_and_can_be_disabled() -> None:
    fullscreen = build_mpv_viewer_argv(_device(), fullscreen=True)
    windowed = build_mpv_viewer_argv(_device(), fullscreen=False)

    assert "--fullscreen" in fullscreen
    assert "--fullscreen" not in windowed


def test_supported_raw_formats_have_explicit_libav_mappings() -> None:
    expected = {
        "YUYV": "yuyv422",
        "UYVY": "uyvy422",
        "NV12": "nv12",
        "YU12": "yuv420p",
        "RGB3": "rgb24",
        "BGR3": "bgr24",
        "H264": "h264",
        "HEVC": "hevc",
    }
    for fourcc, mapped in expected.items():
        argv = build_mpv_viewer_argv(_device(fourcc), fullscreen=False)
        option = next(arg for arg in argv if arg.startswith("--demuxer-lavf-o="))
        assert option.startswith(f"--demuxer-lavf-o=input_format={mapped},")


def test_unknown_driver_format_fails_closed() -> None:
    with pytest.raises(UnsupportedCaptureFormat, match="No reviewed mpv/libav mapping"):
        build_mpv_viewer_argv(_device("ABCD"))


def test_executable_must_not_be_blank() -> None:
    with pytest.raises(ValueError, match="executable must not be blank"):
        build_mpv_viewer_argv(_device(), executable=" ")


def test_yv12_fails_closed_without_a_verified_matching_pixel_format() -> None:
    with pytest.raises(UnsupportedCaptureFormat, match="No reviewed mpv/libav mapping"):
        build_mpv_viewer_argv(_device("YV12"))


def test_executable_path_is_trimmed_before_argv_construction() -> None:
    argv = build_mpv_viewer_argv(_device(), executable="  /usr/bin/mpv  ")
    assert argv[0] == "/usr/bin/mpv"


def test_frame_health_script_is_an_explicit_absolute_mpv_argument() -> None:
    argv = build_mpv_viewer_argv(
        _device(),
        ipc_socket_path="/run/user/1000/hearth/mpv.sock",
        frame_health_script_path="/run/user/1000/hearth/frame-health.lua",
        log_file_path="/run/user/1000/hearth/mpv.log",
    )

    assert "--script=/run/user/1000/hearth/frame-health.lua" in argv
    assert "--osd-level=0" in argv
    assert "--no-osc" in argv
    assert "--force-window=yes" in argv
    assert "--idle=yes" in argv
    assert "--keep-open=yes" in argv
    assert "--log-file=/run/user/1000/hearth/mpv.log" in argv
    assert "--msg-level=all=warn" in argv
    with pytest.raises(ValueError, match="script path must be absolute"):
        build_mpv_viewer_argv(_device(), frame_health_script_path="relative.lua")
    with pytest.raises(ValueError, match="log file path must be absolute"):
        build_mpv_viewer_argv(_device(), log_file_path="relative.log")


def test_mpv_viewer_adapter_builds_and_delegates_to_owned_process_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = Path(tempfile.mkdtemp(prefix="hearth-test-runtime-"))
    runtime.chmod(0o700)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))
    calls: list[list[str]] = []

    class FakeHandle:
        def stop(self, *, timeout: float = 1.0) -> int:
            calls.append(["STOP", str(timeout)])
            return 0

    def factory(argv):
        calls.append(list(argv))
        return FakeHandle()

    adapter = MpvCaptureViewer(executable="/usr/bin/mpv", fullscreen=False, process_factory=factory)
    handle = adapter.start(_device())

    assert isinstance(handle, MpvViewerProcess)
    assert calls[0][0] == "/usr/bin/mpv"
    assert calls[0][-2:] == ["--", "av://v4l2:/dev/video42"]
    assert "--fullscreen" not in calls[0]
    ipc_arg = next(arg for arg in calls[0] if arg.startswith("--input-ipc-server="))
    session_dir = Path(ipc_arg.split("=", 1)[1]).parent
    script_arg = next(arg for arg in calls[0] if arg.startswith("--script="))
    script_path = Path(script_arg.split("=", 1)[1])
    log_arg = next(arg for arg in calls[0] if arg.startswith("--log-file="))
    log_path = Path(log_arg.split("=", 1)[1])
    assert session_dir.parent == runtime and session_dir.exists()
    assert script_path.parent == session_dir and script_path.exists()
    assert script_path.stat().st_mode & 0o777 == 0o600
    assert "screenshot-raw" in script_path.read_text(encoding="utf-8")
    assert log_path.parent == session_dir and log_path.exists()
    assert log_path.stat().st_mode & 0o777 == 0o600
    handle.stop()
    assert not session_dir.exists()
    assert calls[-1] == ["STOP", "1.0"]
    shutil.rmtree(runtime)


def test_mpv_viewer_adapter_removes_private_directory_when_process_start_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = Path(tempfile.mkdtemp(prefix="hearth-test-runtime-fail-"))
    runtime.chmod(0o700)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))

    def fail_factory(_argv):
        raise RuntimeError("synthetic exec failure")

    adapter = MpvCaptureViewer(process_factory=fail_factory)
    with pytest.raises(RuntimeError, match="synthetic exec failure"):
        adapter.start(_device())

    assert list(runtime.iterdir()) == []
    shutil.rmtree(runtime)
