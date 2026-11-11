"""Private mpv IPC and first-decoded-frame readiness for Capture Mode.

The IPC socket is local and unauthenticated, so it lives inside a per-session
0700 directory under XDG_RUNTIME_DIR. The client exists only while Start is
waiting for the first frame; it does not create a watcher or background thread.
"""

from __future__ import annotations

import json
import math
import os
import select
import shutil
import socket
import stat
import tempfile
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Protocol

from hearth.capture_session import FrameHealthState
from hearth.errors import HearthError

FRAME_HEALTH_FREEZE_DURATION_S = 5.0
FRAME_HEALTH_CHANGING_CONFIRM_S = 6.5
FRAME_HEALTH_FILTER_LABEL = "hearth-frame-health"
FRAME_HEALTH_FILTER_GRAPH = f"freezedetect=n=-55dB:d={FRAME_HEALTH_FREEZE_DURATION_S:g}"
FRAME_HEALTH_METADATA_PROPERTY = f"vf-metadata/{FRAME_HEALTH_FILTER_LABEL}"
FRAME_HEALTH_FILTER = f"--vf=@{FRAME_HEALTH_FILTER_LABEL}:lavfi=[{FRAME_HEALTH_FILTER_GRAPH}]"


def _finite_number(value: object) -> float | None:
    """Parse a finite numeric timestamp from mpv/FFmpeg metadata."""
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        number = float(value)
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def _has_frame_health_filter(filters: object) -> bool:
    """Confirm the exact labelled lavfi filter is present and enabled."""
    if not isinstance(filters, list):
        return False
    return any(
        isinstance(item, Mapping)
        and item.get("name") == "lavfi"
        and item.get("label") == FRAME_HEALTH_FILTER_LABEL
        and item.get("enabled") is True
        and isinstance(item.get("params"), Mapping)
        and item["params"].get("graph") == FRAME_HEALTH_FILTER_GRAPH
        for item in filters
    )


class OwnedProcessHandle(Protocol):
    """Minimal owned process API required by the viewer wrapper."""

    def poll(self) -> int | None: ...

    def wait(self, timeout: float | None = None) -> int: ...

    def stop(self, *, timeout: float = 1.0) -> int: ...


class ViewerIPCError(HearthError):
    """The local mpv IPC endpoint was unsafe or spoke an invalid protocol."""


class ViewerReadinessTimeout(HearthError):
    """mpv did not produce a decoded video frame before the deadline."""


class ViewerExitedBeforeFirstFrame(HearthError):
    """The owned mpv process exited before a decoded frame was available."""


class ViewerStreamStalled(HearthError):
    """mpv stopped reporting video timestamp progress during an active session."""


@dataclass(frozen=True, slots=True)
class FirstFrame:
    """Evidence that mpv has a decoded current frame and configured video output."""

    width: int
    height: int
    picture_type: str


def create_private_ipc_directory() -> Path:
    """Create a session-private IPC directory under the owner's runtime dir."""
    runtime = os.environ.get("XDG_RUNTIME_DIR", "").strip()
    if not runtime:
        raise ViewerIPCError("XDG_RUNTIME_DIR is unavailable; refusing an insecure IPC fallback")
    root = Path(runtime)
    try:
        metadata = root.stat()
    except OSError as exc:
        raise ViewerIPCError(f"XDG_RUNTIME_DIR cannot be inspected: {exc}") from exc
    if not stat.S_ISDIR(metadata.st_mode):
        raise ViewerIPCError("XDG_RUNTIME_DIR is not a directory")
    if metadata.st_uid != os.getuid():
        raise ViewerIPCError("XDG_RUNTIME_DIR is not owned by the current user")
    if metadata.st_mode & 0o077:
        raise ViewerIPCError("XDG_RUNTIME_DIR permissions are not private")
    try:
        directory = Path(tempfile.mkdtemp(prefix="hearth-capture-", dir=root))
        directory.chmod(0o700)
    except OSError as exc:
        raise ViewerIPCError(f"Could not create a private viewer IPC directory: {exc}") from exc
    return directory


class MpvViewerProcess:
    """Owned mpv child plus a private JSON IPC path for bounded readiness checks."""

    def __init__(
        self,
        process: OwnedProcessHandle,
        runtime_directory: Path,
        ipc_socket: Path,
    ) -> None:
        self._process = process
        self.runtime_directory = runtime_directory
        self.ipc_socket = ipc_socket
        self._cleaned = False
        self._frame_health_lock = Lock()
        self._frame_health = FrameHealthState.UNKNOWN
        self._frame_health_callback: Callable[[FrameHealthState], None] | None = None

    @property
    def frame_health(self) -> FrameHealthState:
        """Most recent decoded-image state; unchanged is not proof of lost signal."""
        with self._frame_health_lock:
            return self._frame_health

    def set_frame_health_callback(self, callback: Callable[[FrameHealthState], None]) -> None:
        """Attach the active-session observer before monitoring starts."""
        with self._frame_health_lock:
            self._frame_health_callback = callback

    def _publish_frame_health(self, state: FrameHealthState) -> None:
        with self._frame_health_lock:
            if self._frame_health is state:
                return
            self._frame_health = state
            callback = self._frame_health_callback
        if callback is not None:
            callback(state)

    @property
    def pid(self) -> int | None:
        return getattr(self._process, "pid", None)

    def poll(self) -> int | None:
        return self._process.poll()

    def wait(self, timeout: float | None = None) -> int:
        return self._process.wait(timeout=timeout)

    def monitor_until_exit(self, stall_timeout_s: float = 2.0) -> int:
        """Observe timestamp progress and decoded-image changes until child exit.

        This runs only in the active session's existing viewer-wait thread,
        after first-frame readiness. One IPC connection observes both time-pos
        and the FFmpeg freezedetect metadata. A sustained unchanged image is
        reported as advisory state; it does not stop capture because a static
        game menu or pause screen can be legitimate. Timestamp stalls remain a
        separate failure that triggers session cleanup.
        """
        if stall_timeout_s <= 0:
            raise ValueError("stall_timeout_s must be positive")
        if self.poll() is not None:
            return self._process.wait(timeout=1.0)
        socket_path = os.fsencode(self.ipc_socket)
        if len(socket_path) >= 100:
            raise ViewerIPCError("mpv IPC socket path is too long for a Unix-domain socket")

        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            connection.connect(str(self.ipc_socket))
        except OSError as exc:
            connection.close()
            if self.poll() is not None:
                return self._process.wait(timeout=1.0)
            raise ViewerIPCError(f"Could not connect to mpv stream monitor: {exc}") from exc

        buffer = bytearray()
        last_position: float | None = None
        first_position: float | None = None
        last_progress = time.monotonic()
        subscriptions = {
            4001: (1, "time-pos"),
            4002: (2, FRAME_HEALTH_METADATA_PROPERTY),
        }
        acknowledged: set[int] = set()
        last_freeze_start: float | None = None
        last_freeze_end: float | None = None
        try:
            for request_id, (property_id, property_name) in subscriptions.items():
                payload = json.dumps(
                    {
                        "command": ["observe_property", property_id, property_name],
                        "request_id": request_id,
                    },
                    separators=(",", ":"),
                )
                connection.sendall((payload + "\n").encode("utf-8"))

            while True:
                remaining = stall_timeout_s - (time.monotonic() - last_progress)
                readable, _, _ = select.select([connection], [], [], max(0.0, remaining))
                if not readable:
                    raise ViewerStreamStalled(
                        "Share Output video timestamps stopped advancing "
                        f"for {stall_timeout_s:.1f}s"
                    )
                chunk = connection.recv(4096)
                if not chunk:
                    return self._process.wait(timeout=1.0)
                buffer.extend(chunk)
                while b"\n" in buffer:
                    line, _, rest = buffer.partition(b"\n")
                    buffer = bytearray(rest)
                    try:
                        message = json.loads(line)
                    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                        raise ViewerIPCError("mpv sent invalid stream-monitor JSON") from exc
                    if not isinstance(message, Mapping):
                        continue

                    response_id = message.get("request_id")
                    if response_id in subscriptions:
                        if message.get("error") != "success":
                            _, property_name = subscriptions[response_id]
                            raise ViewerIPCError(
                                f"mpv could not observe {property_name}: {message.get('error')}"
                            )
                        acknowledged.add(response_id)
                        if response_id == 4001:
                            last_progress = time.monotonic()
                        continue

                    if message.get("event") != "property-change":
                        continue

                    event_id = message.get("id")
                    if event_id == 1:
                        position = _finite_number(message.get("data"))
                        if position is None:
                            continue
                        if last_position is None or position != last_position:
                            if first_position is None:
                                first_position = position
                            last_position = position
                            if 4001 in acknowledged:
                                last_progress = time.monotonic()
                            # The filter confirms a freeze after five seconds.
                            # Wait an additional 1.5s before inferring motion from
                            # the absence of a freeze event, avoiding a race
                            # between the time-pos and metadata notifications.
                            if (
                                first_position is not None
                                and position - first_position >= FRAME_HEALTH_CHANGING_CONFIRM_S
                                and last_freeze_start is None
                            ):
                                self._publish_frame_health(FrameHealthState.CHANGING)
                    elif event_id == 2:
                        metadata = message.get("data")
                        if not isinstance(metadata, Mapping):
                            continue
                        freeze_start = _finite_number(
                            metadata.get("lavfi.freezedetect.freeze_start")
                        )
                        freeze_end = _finite_number(metadata.get("lavfi.freezedetect.freeze_end"))
                        if freeze_start is not None and freeze_start != last_freeze_start:
                            last_freeze_start = freeze_start
                            self._publish_frame_health(FrameHealthState.UNCHANGED)
                        if freeze_end is not None and freeze_end != last_freeze_end:
                            last_freeze_end = freeze_end
                            self._publish_frame_health(FrameHealthState.CHANGING)
        except ViewerStreamStalled:
            raise
        except (ConnectionError, OSError) as exc:
            if self.poll() is not None:
                return self._process.wait(timeout=1.0)
            raise ViewerIPCError(f"mpv stream monitor connection failed: {exc}") from exc
        finally:
            connection.close()

    def wait_for_first_frame(self, timeout_s: float) -> FirstFrame:
        """Wait for decoded frame metadata and a configured video output.

        mpv's `video-frame-info/picture-type` is unavailable until it has a
        current frame. We require a valid picture type, positive decoded frame
        dimensions, and `vo-configured=true`; a running PID or an open input
        alone does not satisfy readiness.
        """
        if timeout_s <= 0:
            raise ValueError("timeout_s must be positive")
        socket_path = os.fsencode(self.ipc_socket)
        if len(socket_path) >= 100:
            raise ViewerIPCError("mpv IPC socket path is too long for a Unix-domain socket")
        deadline = time.monotonic() + timeout_s
        while True:
            if self.poll() is not None:
                raise ViewerExitedBeforeFirstFrame(
                    "mpv exited before its first decoded video frame"
                )
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ViewerReadinessTimeout(
                    "Timed out waiting for mpv's first decoded video frame"
                )

            connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            connection.settimeout(min(0.2, remaining))
            try:
                connection.connect(str(self.ipc_socket))
            except (FileNotFoundError, ConnectionRefusedError, TimeoutError):
                connection.close()
                time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
                continue
            except OSError as exc:
                connection.close()
                raise ViewerIPCError(f"Could not connect to mpv IPC: {exc}") from exc

            try:
                with connection.makefile("rwb", buffering=0) as channel:
                    picture = self._get_property(
                        connection, channel, "video-frame-info/picture-type", 1, deadline
                    )
                    if picture not in {"I", "P", "B"}:
                        continue
                    parameters = self._get_property(
                        connection, channel, "video-params", 2, deadline
                    )
                    output_ready = self._get_property(
                        connection, channel, "vo-configured", 3, deadline
                    )
                    frame_properties_valid = (
                        isinstance(parameters, dict)
                        and isinstance(parameters.get("w"), int)
                        and isinstance(parameters.get("h"), int)
                        and parameters["w"] > 0
                        and parameters["h"] > 0
                        and output_ready is True
                    )
                    filters = (
                        self._get_property(connection, channel, "vf", 4, deadline)
                        if frame_properties_valid
                        else None
                    )
            except (ConnectionError, BrokenPipeError, OSError, TimeoutError, ValueError) as exc:
                if self.poll() is not None:
                    raise ViewerExitedBeforeFirstFrame(
                        "mpv exited while checking first-frame readiness"
                    ) from exc
                if time.monotonic() >= deadline:
                    raise ViewerReadinessTimeout(
                        "Timed out while reading mpv first-frame properties"
                    ) from exc
                continue
            finally:
                connection.close()

            if (
                isinstance(parameters, dict)
                and isinstance(parameters.get("w"), int)
                and isinstance(parameters.get("h"), int)
                and parameters["w"] > 0
                and parameters["h"] > 0
                and output_ready is True
            ):
                if not _has_frame_health_filter(filters):
                    raise ViewerIPCError("mpv did not enable the required frame-health filter")
                return FirstFrame(parameters["w"], parameters["h"], picture)
            time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))

    @staticmethod
    def _get_property(
        connection: socket.socket,
        channel: socket.SocketIO,
        name: str,
        request_id: int,
        deadline: float,
    ) -> object:
        """Send one JSON IPC query and wait for its matching response."""
        payload = json.dumps(
            {"command": ["get_property", name], "request_id": request_id},
            separators=(",", ":"),
        )
        channel.write((payload + "\n").encode("utf-8"))
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("mpv IPC response deadline expired")
            connection.settimeout(min(0.2, remaining))
            try:
                raw = channel.readline()
            except TimeoutError:
                continue
            if not raw:
                raise ConnectionError("mpv closed the IPC connection")
            try:
                response = json.loads(raw)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ViewerIPCError("mpv sent invalid JSON IPC data") from exc
            if not isinstance(response, Mapping) or response.get("request_id") != request_id:
                # Property notifications or unrelated messages are not replies
                # to the outstanding request; keep the connection and wait.
                continue
            if response.get("error") == "success":
                return response.get("data")
            if response.get("error") == "property unavailable":
                return None
            raise ViewerIPCError(f"mpv property {name!r} failed: {response.get('error')}")

    def stop(self, *, timeout: float = 1.0) -> int:
        """Stop/reap mpv, then remove its private IPC directory."""
        result = self._process.stop(timeout=timeout)
        if not self._cleaned:
            try:
                shutil.rmtree(self.runtime_directory)
            except FileNotFoundError:
                pass
            except OSError as exc:
                raise ViewerIPCError(
                    f"Could not remove private viewer IPC directory: {exc}"
                ) from exc
            self._cleaned = True
        return result
