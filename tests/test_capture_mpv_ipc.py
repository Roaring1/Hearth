"""mpv IPC readiness tests use a fake Unix socket server and fake child handle."""

from __future__ import annotations

import json
import os
import socket
import stat
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from hearth.capture_mpv_ipc import (
    FirstFrame,
    MpvViewerProcess,
    ViewerExitedBeforeFirstFrame,
    ViewerIPCError,
    ViewerReadinessTimeout,
    ViewerStreamStalled,
    create_private_ipc_directory,
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


def test_first_frame_aborts_if_owned_viewer_exits(tmp_path: Path) -> None:
    viewer = _viewer(tmp_path, process=FakeProcess(exit_code=7))

    with pytest.raises(ViewerExitedBeforeFirstFrame):
        viewer.wait_for_first_frame(timeout_s=1.0)


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
    ) -> None:
        self.path = path
        self.process = process
        self.positions = positions
        self.hold_open = hold_open
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
                while b"\n" not in request_buffer:
                    request_buffer += client.recv(4096)
                request_line, _, _ = request_buffer.partition(b"\n")
                request = json.loads(request_line)
                assert request["command"] == ["observe_property", 1, "time-pos"]
                client.sendall(
                    (
                        json.dumps(
                            {
                                "request_id": request["request_id"],
                                "error": "success",
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
                if self.hold_open:
                    self.stop_event.wait(2.0)
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


def test_stream_monitor_fails_when_time_pos_stops_advancing(tmp_path: Path) -> None:
    process = FakeProcess()
    viewer = _viewer(tmp_path, process=process)

    with (
        FakeFrameMonitorServer(viewer.ipc_socket, process, [0.1], hold_open=True),
        pytest.raises(ViewerStreamStalled, match="timestamps stopped advancing"),
    ):
        viewer.monitor_until_exit(stall_timeout_s=0.12)
