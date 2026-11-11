"""Pure mpv command construction for the future video-only capture viewer.

No process is started here. The process owner/parent-death adapter is a
separate concern and must be in place before this argv is ever launched.
"""

from __future__ import annotations

from hearth.capture_session import ResolvedCaptureDevice
from hearth.errors import HearthError


class UnsupportedCaptureFormat(HearthError):
    """The enumerated V4L2 FourCC has no reviewed libav input mapping."""


# Mapping is deliberately allow-listed. Unknown driver-specific FourCCs must
# not be guessed into an FFmpeg codec/pixel format.
_V4L2_TO_LIBAV_INPUT_FORMAT: dict[str, str] = {
    "MJPG": "mjpeg",
    "JPEG": "mjpeg",
    "H264": "h264",
    "HEVC": "hevc",
    "YUYV": "yuyv422",
    "UYVY": "uyvy422",
    "NV12": "nv12",
    "YU12": "yuv420p",
    "RGB3": "rgb24",
    "BGR3": "bgr24",
}


def build_mpv_viewer_argv(
    device: ResolvedCaptureDevice,
    *,
    executable: str = "mpv",
    fullscreen: bool = True,
) -> list[str]:
    """Build a no-audio, no-OSD mpv command for one resolved V4L2 mode.

    The selected mode is passed explicitly to FFmpeg's V4L2 libavdevice
    demuxer. The device path is ephemeral and comes from a fresh probe, never
    from persisted CapturePlan state. Fullscreen is only a compositor request;
    Wayland output placement and window-capture behavior remain unverified.
    """
    executable = executable.strip()
    if not executable:
        raise ValueError("mpv executable must not be blank")
    pixel_format = _V4L2_TO_LIBAV_INPUT_FORMAT.get(device.mode.pixel_format)
    if pixel_format is None:
        raise UnsupportedCaptureFormat(
            f"No reviewed mpv/libav mapping for V4L2 format {device.mode.pixel_format!r}"
        )
    width = device.mode.width
    height = device.mode.height
    fps = f"{device.mode.fps_numerator}/{device.mode.fps_denominator}"
    lavf_options = f"input_format={pixel_format},video_size={width}x{height},framerate={fps}"

    argv = [
        executable,
        "--no-config",
        "--title=Hearth - Share Output",
        "--audio=no",
        "--no-osc",
        "--osd-level=0",
        "--no-sub",
        "--cursor-autohide=always",
        "--input-builtin-bindings=no",
        f"--demuxer-lavf-o={lavf_options}",
        "--force-window=yes",
    ]
    if fullscreen:
        argv.append("--fullscreen")
    argv.extend(("--", f"av://v4l2:{device.video_node_path}"))
    return argv
