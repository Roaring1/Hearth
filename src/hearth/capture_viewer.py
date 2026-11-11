"""mpv command construction and explicit start adapter for Capture Mode.

Building argv is pure. `MpvCaptureViewer.start()` launches only when called
through the owned `proc.spawn_owned` boundary; constructing this adapter or
importing this module starts no process and probes no device.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable, Sequence
from pathlib import Path

from hearth import proc
from hearth.capture_frame_health import write_frame_health_script
from hearth.capture_mpv_ipc import (
    MpvViewerProcess,
    create_private_ipc_directory,
    create_private_mpv_log_file,
)
from hearth.capture_session import ResolvedCaptureDevice, ViewerHandle
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
    ipc_socket_path: str | Path | None = None,
    frame_health_script_path: str | Path | None = None,
    log_file_path: str | Path | None = None,
) -> list[str]:
    """Build a no-audio, video-only mpv command with a conditional No signal OSD.

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
        "--idle=yes",
        "--keep-open=yes",
    ]
    if ipc_socket_path is not None:
        ipc_path = str(ipc_socket_path)
        if not ipc_path.startswith("/"):
            raise ValueError("mpv IPC socket path must be absolute")
        if len(os.fsencode(ipc_path)) >= 100:
            raise ValueError("mpv IPC socket path is too long")
        argv.append(f"--input-ipc-server={ipc_path}")
    if frame_health_script_path is not None:
        script_path = str(frame_health_script_path)
        if not script_path.startswith("/"):
            raise ValueError("mpv frame-health script path must be absolute")
        argv.append(f"--script={script_path}")
    if log_file_path is not None:
        log_path = str(log_file_path)
        if not log_path.startswith("/"):
            raise ValueError("mpv log file path must be absolute")
        argv.extend((f"--log-file={log_path}", "--msg-level=all=warn"))
    if fullscreen:
        argv.append("--fullscreen")
    argv.extend(("--", f"av://v4l2:{device.video_node_path}"))
    return argv


class MpvCaptureViewer:
    """The capture runtime's mpv start_viewer adapter.

    Construction is inert. Calling `start()` is the explicit session action
    that launches through `proc.spawn_owned`; the adapter itself does not probe
    devices or decide when a first frame has arrived.
    """

    def __init__(
        self,
        *,
        executable: str = "mpv",
        fullscreen: bool = True,
        process_factory: Callable[[Sequence[str]], ViewerHandle] = proc.spawn_owned,
    ) -> None:
        self._executable = executable
        self._fullscreen = fullscreen
        self._process_factory = process_factory

    def start(self, device: ResolvedCaptureDevice) -> ViewerHandle:
        """Build the explicit V4L2 command and return its owned IPC/process handle."""
        runtime_directory = create_private_ipc_directory()
        ipc_socket = runtime_directory / "mpv.sock"
        frame_health_script = None
        mpv_log = None
        try:
            frame_health_script = write_frame_health_script(runtime_directory)
            mpv_log = create_private_mpv_log_file(runtime_directory)
            argv = build_mpv_viewer_argv(
                device,
                executable=self._executable,
                fullscreen=self._fullscreen,
                ipc_socket_path=ipc_socket,
                frame_health_script_path=frame_health_script,
                log_file_path=mpv_log,
            )
            process = self._process_factory(argv)
        except Exception:
            shutil.rmtree(runtime_directory, ignore_errors=True)
            raise
        return MpvViewerProcess(process, runtime_directory, ipc_socket)
