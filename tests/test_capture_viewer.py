"""Pure mpv argv tests; never open a video node or start mpv."""

from __future__ import annotations

import pytest

from hearth.capture_session import ResolvedCaptureDevice, UsbIdentity, VideoMode
from hearth.capture_viewer import UnsupportedCaptureFormat, build_mpv_viewer_argv


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
