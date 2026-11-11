"""mpv IPC readiness tests use a fake Unix socket server and fake child handle."""

from __future__ import annotations

import json
import os
import socket
import stat
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from hearth.capture_mpv_ipc import (
    FirstFrame,
    MpvViewerProcess,
    ViewerInputOpenError,
    ViewerIPCError,
    ViewerReadinessTimeout,
    classify_mpv_input_error,
    cleanup_stale_capture_ipc_directories,
    create_private_ipc_directory,
    create_private_mpv_log_file,
)


class FakeProcess:
    def __init__(self, exit_code: int | None = None) -> None:
        self.exit_code = exit_code
        self.stopped = False

    def poll(self) -> int | None:
        return self.exit_code

    def wait(self, timeout: float | None = None) -> int:
        if self.exit_code is None:
            raise TimeoutError("still running")
        return self.exit_code

    def stop(self, *, timeout: float = 1.0) -> int:
        self.stopped = True
        if self.exit_code is None:
            self.exit_code = -15
        return self.exit_code


class FakeMpvIpcServer:
    def __init__(self, path: Path, handler: Any) -> None:
        self.path = path
        self.handler = handler
        self.stop_event = threading.Event()
        self.errors: list[BaseException] = []
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server.bind(str(path))
        self.server.listen(4)
        self.server.settimeout(0.05)
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self) -> None:
        try:
            while not self.stop_event.is_set():
                try:
                    client, _ = self.server.accept()
                except TimeoutError:
                    continue
                with client:
                    client.settimeout(0.5)
                    buffer = b""
                    while not self.stop_event.is_set():
                        try:
                            chunk = client.recv(4096)
                        except TimeoutError:
                            continue
                        if not chunk:
                            break
                        buffer += chunk
                        while bytes([10]) in buffer:
                            line, buffer = buffer.split(bytes([10]), 1)
                            request = json.loads(line)
                            response = self.handler(request)
                            if response is not None:
                                client.sendall((json.dumps(response) + "\n").encode())
        except (BrokenPipeError, ConnectionResetError):
            # The client may close deliberately when its readiness deadline expires.
            pass
        except BaseException as exc:
            self.errors.append(exc)
        finally:
            self.server.close()

    def close(self) -> None:
        self.stop_event.set()
        self.thread.join(timeout=1)

    def __enter__(self) -> FakeMpvIpcServer:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
        assert self.errors == []


def _viewer(tmp_path: Path, process: FakeProcess | None = None) -> MpvViewerProcess:
    runtime = tmp_path / "session"
    runtime.mkdir(mode=0o700)
    runtime.chmod(0o700)
    return MpvViewerProcess(process or FakeProcess(), runtime, runtime / "mpv.sock")


def test_stale_capture_directory_sweep_removes_only_old_private_dirs(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    runtime.mkdir(mode=0o700)
    runtime.chmod(0o700)
    stale = runtime / "hearth-capture-stale"
    stale.mkdir(mode=0o700)
    script = stale / "frame-health.lua"
    script.write_text("stale")
    script.chmod(0o600)
    fresh = runtime / "hearth-capture-fresh"
    fresh.mkdir(mode=0o700)
    permissive = runtime / "hearth-capture-permissive"
    permissive.mkdir(mode=0o755)
    permissive.chmod(0o755)
    symlink = runtime / "hearth-capture-link"
    symlink.symlink_to(stale, target_is_directory=True)
    now = time.time()
    os.utime(stale, (now - 120, now - 120))
    os.utime(fresh, (now - 10, now - 10))
    os.utime(permissive, (now - 120, now - 120))

    removed = cleanup_stale_capture_ipc_directories(runtime, min_age_s=60, now=now)

    assert removed == (stale,)
    assert not stale.exists()
    assert fresh.is_dir()
    assert permissive.is_dir()
    assert symlink.is_symlink()


def test_stale_capture_directory_sweep_preserves_unknown_user_content(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    runtime.mkdir(mode=0o700)
    runtime.chmod(0o700)
    unrelated = runtime / "hearth-capture-user-data"
    unrelated.mkdir(mode=0o700)
    marker = unrelated / "keep.txt"
    marker.write_text("user-owned")
    now = time.time()
    os.utime(unrelated, (now - 120, now - 120))

    assert cleanup_stale_capture_ipc_directories(runtime, min_age_s=60, now=now) == ()
    assert marker.read_text(encoding="utf-8") == "user-owned"


def test_stale_capture_directory_sweep_removes_refused_mpv_socket() -> None:
    with tempfile.TemporaryDirectory(prefix="hc-", dir="/tmp") as temporary:
        runtime = Path(temporary) / "r"
        runtime.mkdir(mode=0o700)
        runtime.chmod(0o700)
        stale = runtime / "hearth-capture-stale-socket"
        stale.mkdir(mode=0o700)
        script = stale / "frame-health.lua"
        script.write_text("stale")
        script.chmod(0o600)
        ipc_path = stale / "mpv.sock"
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(str(ipc_path))
        server.close()
        now = time.time()
        os.utime(stale, (now - 120, now - 120))

        removed = cleanup_stale_capture_ipc_directories(runtime, min_age_s=60, now=now)

        assert removed == (stale,)
        assert not stale.exists()


def test_stale_capture_directory_sweep_preserves_live_mpv_socket(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    runtime.mkdir(mode=0o700)
    runtime.chmod(0o700)
    active = runtime / "hearth-capture-active"
    active.mkdir(mode=0o700)
    ipc_path = active / "mpv.sock"
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(ipc_path))
    server.listen(1)
    now = time.time()
    os.utime(active, (now - 120, now - 120))
    try:
        removed = cleanup_stale_capture_ipc_directories(runtime, min_age_s=60, now=now)
        assert removed == ()
        assert active.is_dir()
    finally:
        server.close()

    removed = cleanup_stale_capture_ipc_directories(runtime, min_age_s=60, now=now)
    assert removed == (active,)
    assert not active.exists()


def test_stale_capture_directory_sweep_fails_closed_for_untrusted_runtime_root(
    tmp_path: Path,
) -> None:
    runtime = tmp_path / "runtime"
    runtime.mkdir(mode=0o755)
    runtime.chmod(0o755)
    stale = runtime / "hearth-capture-stale"
    stale.mkdir(mode=0o700)
    now = time.time()
    os.utime(stale, (now - 120, now - 120))

    assert cleanup_stale_capture_ipc_directories(runtime, min_age_s=60, now=now) == ()
    assert stale.is_dir()


def test_private_ipc_directory_requires_current_users_private_runtime_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    with monkeypatch.context() as context:
        context.delenv("XDG_RUNTIME_DIR", raising=False)
        with pytest.raises(ViewerIPCError, match="XDG_RUNTIME_DIR is unavailable"):
            create_private_ipc_directory()

    runtime = tmp_path / "runtime"
    runtime.mkdir(mode=0o700)
    runtime.chmod(0o700)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))

    directory = create_private_ipc_directory()
    try:
        metadata = directory.stat()
        assert stat.S_IMODE(metadata.st_mode) == 0o700
        assert metadata.st_uid == os.getuid()
        assert directory.parent == runtime
    finally:
        directory.rmdir()


def test_private_mpv_log_file_is_created_mode_0600_and_never_overwritten(
    tmp_path: Path,
) -> None:
    runtime = tmp_path / "runtime"
    runtime.mkdir(mode=0o700)
    runtime.chmod(0o700)

    log_path = create_private_mpv_log_file(runtime)

    assert log_path.exists()
    assert stat.S_IMODE(log_path.stat().st_mode) == 0o600
    log_path.write_text("existing", encoding="utf-8")
    with pytest.raises(ViewerIPCError, match="File exists"):
        create_private_mpv_log_file(runtime)
    assert log_path.read_text(encoding="utf-8") == "existing"


def test_create_private_ipc_directory_runs_stale_sweep_only_on_explicit_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = tmp_path / "runtime"
    runtime.mkdir(mode=0o700)
    runtime.chmod(0o700)
    stale = runtime / "hearth-capture-stale"
    stale.mkdir(mode=0o700)
    now = time.time()
    os.utime(stale, (now - 120, now - 120))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))

    created = create_private_ipc_directory()

    assert not stale.exists()
    assert created.is_dir()
    assert stat.S_IMODE(created.stat().st_mode) == 0o700


def test_private_ipc_directory_rejects_group_or_world_access(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    runtime = tmp_path / "runtime"
    runtime.mkdir(mode=0o755)
    runtime.chmod(0o755)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))

    with pytest.raises(ViewerIPCError, match="permissions are not private"):
        create_private_ipc_directory()


def test_first_frame_requires_picture_type_dimensions_and_configured_output(
    tmp_path: Path,
) -> None:
    viewer = _viewer(tmp_path)
    seen: list[str] = []

    def handler(request: dict[str, Any]) -> dict[str, object]:
        name = request["command"][1]
        seen.append(name)
        values: dict[str, object] = {
            "video-frame-info/picture-type": "I",
            "video-params": {"w": 1920, "h": 1080, "pixelformat": "yuv420p"},
            "vo-configured": True,
        }
        return {
            "request_id": request["request_id"],
            "error": "success",
            "data": values[name],
        }

    with FakeMpvIpcServer(viewer.ipc_socket, handler):
        frame = viewer.wait_for_first_frame(timeout_s=1.0)

    assert frame == FirstFrame(1920, 1080, "I")
    assert seen == ["video-frame-info/picture-type", "video-params", "vo-configured"]


@pytest.mark.parametrize(
    ("detail", "category"),
    [
        ("Device or resource busy", "device_busy"),
        ("Permission denied", "permission_denied"),
        ("No such file or directory", "device_unavailable"),
        ("Invalid argument", "unsupported_mode"),
        ("Input/output error", "io_error"),
        ("Unclassified decoder failure", "open_failed"),
    ],
)
def test_mpv_end_file_errors_map_to_actionable_device_categories(
    detail: str, category: str
) -> None:
    error = classify_mpv_input_error("error", detail)

    assert isinstance(error, ViewerInputOpenError)
    assert error.category == category
    assert error.detail == detail
    assert error.user_message


def test_mpv_error_classifier_uses_private_log_detail_when_ipc_error_is_generic() -> None:
    error = classify_mpv_input_error(
        "error",
        "loading failed",
        "[e][file] Cannot open file '/dev/video999': No such file or directory",
    )

    assert isinstance(error, ViewerInputOpenError)
    assert error.category == "device_unavailable"
    assert "No such file" in error.detail


def test_non_error_end_file_event_is_not_misclassified() -> None:
    assert classify_mpv_input_error("eof", None) is None
    assert classify_mpv_input_error("stop", "") is None
    stale_log = "[e][file] stale prior failure: No such file or directory"
    assert classify_mpv_input_error("eof", "loading failed", stale_log) is None
    assert classify_mpv_input_error(None, "loading failed", stale_log) is None
    assert classify_mpv_input_error(7, "loading failed", stale_log) is None
    assert classify_mpv_input_error("timeout", "", stale_log) is None


def test_first_frame_uses_private_mpv_log_to_classify_generic_loading_failed(
    tmp_path: Path,
) -> None:
    viewer = _viewer(tmp_path)
    (viewer.runtime_directory / "mpv.log").write_text(
        "[e][file] Cannot open file '/dev/video999': No such file or directory\n"
        "[e][stream] Failed to open /dev/video999.\n",
        encoding="utf-8",
    )

    def handler(_request: dict[str, Any]) -> dict[str, object]:
        return {"event": "end-file", "reason": "error", "file_error": "loading failed"}

    with (
        FakeMpvIpcServer(viewer.ipc_socket, handler),
        pytest.raises(ViewerInputOpenError) as caught,
    ):
        viewer.wait_for_first_frame(timeout_s=1.0)

    assert caught.value.category == "device_unavailable"


def test_first_frame_timeout_does_not_classify_log_without_end_file_error(
    tmp_path: Path,
) -> None:
    viewer = _viewer(tmp_path)
    (viewer.runtime_directory / "mpv.log").write_text(
        "[e][v4l2] Device or resource busy\n",
        encoding="utf-8",
    )

    def handler(request: dict[str, Any]) -> dict[str, object]:
        return {"request_id": request["request_id"], "error": "property unavailable"}

    with (
        FakeMpvIpcServer(viewer.ipc_socket, handler),
        pytest.raises(ViewerReadinessTimeout),
    ):
        viewer.wait_for_first_frame(timeout_s=0.1)


def test_first_frame_surfaces_device_busy_from_mpv_end_file_event(tmp_path: Path) -> None:
    viewer = _viewer(tmp_path)

    def handler(_request: dict[str, Any]) -> dict[str, object]:
        return {
            "event": "end-file",
            "reason": "error",
            "file_error": "Device or resource busy",
        }

    with (
        FakeMpvIpcServer(viewer.ipc_socket, handler),
        pytest.raises(ViewerInputOpenError) as caught,
    ):
        viewer.wait_for_first_frame(timeout_s=1.0)

    assert caught.value.category == "device_busy"
    assert "already in use" in caught.value.user_message


def test_first_frame_retries_unavailable_frame_properties(tmp_path: Path) -> None:
    viewer = _viewer(tmp_path)
    picture_reads = 0

    def handler(request: dict[str, Any]) -> dict[str, object]:
        nonlocal picture_reads
        name = request["command"][1]
        if name == "video-frame-info/picture-type":
            picture_reads += 1
            if picture_reads == 1:
                return {"request_id": request["request_id"], "error": "property unavailable"}
            data: object = "P"
        elif name == "video-params":
            data = {"w": 640, "h": 360}
        else:
            data = True
        return {"request_id": request["request_id"], "error": "success", "data": data}

    with FakeMpvIpcServer(viewer.ipc_socket, handler):
        frame = viewer.wait_for_first_frame(timeout_s=1.0)

    assert frame == FirstFrame(640, 360, "P")
    assert picture_reads >= 2


@pytest.mark.parametrize(
    ("picture", "dimensions", "vo_ready"),
    [
        (None, {"w": 640, "h": 360}, True),
        ("I", {"w": 0, "h": 360}, True),
        ("I", {"w": 640, "h": 360}, False),
    ],
)
def test_first_frame_rejects_partial_readiness(
    tmp_path: Path, picture: object, dimensions: object, vo_ready: bool
) -> None:
    viewer = _viewer(tmp_path)

    def handler(request: dict[str, Any]) -> dict[str, object]:
        name = request["command"][1]
        data = {
            "video-frame-info/picture-type": picture,
            "video-params": dimensions,
            "vo-configured": vo_ready,
        }[name]
        return {"request_id": request["request_id"], "error": "success", "data": data}

    with FakeMpvIpcServer(viewer.ipc_socket, handler), pytest.raises(ViewerReadinessTimeout):
        viewer.wait_for_first_frame(timeout_s=0.12)


def test_first_frame_reports_generic_input_error_when_mpv_exits_nonzero(
    tmp_path: Path,
) -> None:
    viewer = _viewer(tmp_path, process=FakeProcess(exit_code=7))

    with pytest.raises(ViewerInputOpenError) as caught:
        viewer.wait_for_first_frame(timeout_s=1.0)

    assert caught.value.category == "open_failed"


def test_first_frame_timeout_when_ipc_socket_never_appears(tmp_path: Path) -> None:
    viewer = _viewer(tmp_path)

    with pytest.raises(ViewerReadinessTimeout):
        viewer.wait_for_first_frame(timeout_s=0.12)


def test_stop_reaps_process_then_removes_only_its_private_ipc_directory(
    tmp_path: Path,
) -> None:
    viewer = _viewer(tmp_path)
    runtime = viewer.runtime_directory
    process = viewer._process

    assert viewer.stop() == -15

    assert process.stopped
    assert not runtime.exists()


def test_socket_response_matching_ignores_unrelated_events(tmp_path: Path) -> None:
    viewer = _viewer(tmp_path)

    def handler(request: dict[str, Any]) -> dict[str, object]:
        return {
            "event": "playback-restart",
            "data": "I",
        }

    with FakeMpvIpcServer(viewer.ipc_socket, handler), pytest.raises(ViewerReadinessTimeout):
        viewer.wait_for_first_frame(timeout_s=0.1)


class FakeFrameMonitorServer:
    """Serve one observe_property subscription, then exit or hold without frames."""

    def __init__(
        self,
        path: Path,
        process: FakeProcess,
        positions: list[float],
        *,
        hold_open: bool,
        frame_health: dict[str, object] | None = None,
        frame_health_error: str | None = None,
        resume_after_no_signal: bool = False,
        end_file_error: bool = False,
    ) -> None:
        self.path = path
        self.process = process
        self.positions = positions
        self.hold_open = hold_open
        self.frame_health = frame_health
        self.frame_health_error = frame_health_error
        self.resume_after_no_signal = resume_after_no_signal
        self.end_file_error = end_file_error
        self.no_signal_entered = threading.Event()
        self.resume_event = threading.Event()
        self.no_signal_transitions: list[bool] = []
        self.stop_event = threading.Event()
        self.errors: list[BaseException] = []
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server.bind(str(path))
        self.server.listen(1)
        self.server.settimeout(0.05)
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self) -> None:
        try:
            client, _ = self.server.accept()
            with client:
                client.settimeout(1.0)
                request_buffer = b""
                requests: list[dict[str, object]] = []
                while len(requests) < 2:
                    while b"\n" not in request_buffer:
                        request_buffer += client.recv(4096)
                    request_line, _, request_buffer = request_buffer.partition(b"\n")
                    requests.append(json.loads(request_line))
                assert {request["command"][1]: request["command"][2] for request in requests} == {
                    1: "time-pos",
                    2: "user-data/hearth/frame-health",
                }
                for request in requests:
                    client.sendall(
                        (
                            json.dumps(
                                {
                                    "request_id": request["request_id"],
                                    "error": (
                                        self.frame_health_error
                                        if request["command"][1] == 2 and self.frame_health_error
                                        else "success"
                                    ),
                                }
                            )
                            + "\n"
                        ).encode()
                    )
                if self.frame_health is not None:
                    client.sendall(
                        (
                            json.dumps(
                                {
                                    "event": "property-change",
                                    "id": 2,
                                    "name": "user-data/hearth/frame-health",
                                    "data": self.frame_health,
                                }
                            )
                            + "\n"
                        ).encode()
                    )
                for position in self.positions:
                    if self.stop_event.is_set():
                        break
                    client.sendall(
                        (
                            json.dumps(
                                {
                                    "event": "property-change",
                                    "id": 1,
                                    "name": "time-pos",
                                    "data": position,
                                }
                            )
                            + "\n"
                        ).encode()
                    )
                    time.sleep(0.02)
                if self.end_file_error:
                    client.sendall(
                        (json.dumps({"event": "end-file", "reason": "error"}) + "\n").encode()
                    )
                if self.resume_after_no_signal:
                    while not self.stop_event.is_set():
                        chunk = client.recv(4096)
                        if not chunk:
                            break
                        request_buffer += chunk
                        while b"\n" in request_buffer:
                            request_line, _, request_buffer = request_buffer.partition(b"\n")
                            message = json.loads(request_line)
                            if message.get("command") == [
                                "set_property",
                                "user-data/hearth/no-signal",
                                True,
                            ]:
                                self.no_signal_transitions.append(True)
                                self.no_signal_entered.set()
                                if not self.resume_event.wait(1.0):
                                    raise TimeoutError("test did not resume the fake stream")
                                client.sendall(
                                    (
                                        json.dumps(
                                            {
                                                "event": "property-change",
                                                "id": 1,
                                                "name": "time-pos",
                                                "data": self.positions[-1] + 0.1,
                                            }
                                        )
                                        + "\n"
                                    ).encode()
                                )
                            elif message.get("command") == [
                                "set_property",
                                "user-data/hearth/no-signal",
                                False,
                            ]:
                                self.no_signal_transitions.append(False)
                                self.process.exit_code = 0
                                return
                elif self.hold_open:
                    self.stop_event.wait(0.3)
                    self.process.exit_code = 0
                else:
                    self.process.exit_code = 0
        except BaseException as exc:
            self.errors.append(exc)
        finally:
            self.server.close()

    def close(self) -> None:
        self.stop_event.set()
        self.thread.join(timeout=1.0)

    def __enter__(self) -> FakeFrameMonitorServer:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
        assert self.errors == []


def test_stream_monitor_uses_time_pos_events_and_returns_when_player_exits(
    tmp_path: Path,
) -> None:
    process = FakeProcess()
    viewer = _viewer(tmp_path, process=process)

    with FakeFrameMonitorServer(viewer.ipc_socket, process, [0.1, 0.2, 0.3, 0.4], hold_open=False):
        assert viewer.monitor_until_exit(stall_timeout_s=0.2) == 0


def test_stream_monitor_shows_no_signal_after_timestamp_stall_and_keeps_waiting(
    tmp_path: Path,
) -> None:
    process = FakeProcess()
    viewer = _viewer(tmp_path, process=process)

    with FakeFrameMonitorServer(viewer.ipc_socket, process, [0.1], hold_open=True):
        assert viewer.monitor_until_exit(stall_timeout_s=0.12) == 0

    assert viewer.no_signal is True


def test_stream_monitor_shows_no_signal_on_input_end_file_error(tmp_path: Path) -> None:
    process = FakeProcess()
    viewer = _viewer(tmp_path, process=process)

    with FakeFrameMonitorServer(
        viewer.ipc_socket, process, [0.1], hold_open=True, end_file_error=True
    ):
        assert viewer.monitor_until_exit(stall_timeout_s=0.5) == 0

    assert viewer.no_signal is True


def test_stream_monitor_clears_no_signal_when_time_pos_resumes(tmp_path: Path) -> None:
    process = FakeProcess()
    viewer = _viewer(tmp_path, process=process)
    result: list[int] = []
    observed: list[bool] = []
    viewer.set_no_signal_callback(observed.append)

    with FakeFrameMonitorServer(
        viewer.ipc_socket, process, [0.1], hold_open=True, resume_after_no_signal=True
    ) as server:
        monitor = threading.Thread(
            target=lambda: result.append(viewer.monitor_until_exit(stall_timeout_s=0.12)),
            daemon=True,
        )
        monitor.start()
        assert server.no_signal_entered.wait(1.0)
        assert viewer.no_signal is True
        server.resume_event.set()
        monitor.join(timeout=1.0)
        assert not monitor.is_alive()

    assert result == [0]
    assert viewer.no_signal is False
    assert server.no_signal_transitions == [True, False]
    assert observed == [True, False]


def test_stream_monitor_exposes_validated_pixel_health_without_failing_the_stream(
    tmp_path: Path,
) -> None:
    process = FakeProcess()
    viewer = _viewer(tmp_path, process=process)
    health = {
        "ready": True,
        "state": "mostly_black",
        "width": 1920,
        "height": 1080,
        "sample_count": 4,
        "black_fraction": 0.995,
        "unchanged_s": 6.0,
        "black_s": 6.0,
    }

    with FakeFrameMonitorServer(
        viewer.ipc_socket, process, [0.1, 0.2, 0.3], hold_open=False, frame_health=health
    ):
        assert viewer.monitor_until_exit(stall_timeout_s=0.2) == 0

    assert viewer.frame_health is not None
    assert viewer.frame_health.state == "mostly_black"
    assert viewer.frame_health.width == 1920
    assert viewer.frame_health.black_fraction == 0.995


def test_stream_monitor_keeps_timestamp_liveness_when_frame_health_is_unavailable(
    tmp_path: Path,
) -> None:
    process = FakeProcess()
    viewer = _viewer(tmp_path, process=process)

    with FakeFrameMonitorServer(
        viewer.ipc_socket,
        process,
        [0.1, 0.2, 0.3],
        hold_open=False,
        frame_health_error="property unavailable",
    ):
        assert viewer.monitor_until_exit(stall_timeout_s=0.2) == 0

    assert viewer.frame_health is None
