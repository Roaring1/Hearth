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
from contextlib import suppress
from dataclasses import dataclass
from errno import ECONNREFUSED, ENOENT
from pathlib import Path
from typing import ClassVar, Protocol

from hearth.capture_frame_health import FrameHealthSnapshot, parse_frame_health_payload
from hearth.errors import HearthError


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


class ViewerInputOpenError(HearthError):
    """mpv reported a classified input/device-open failure before first frame."""

    _USER_MESSAGES: ClassVar[dict[str, str]] = {
        "device_busy": (
            "The capture device is already in use. Close other apps using it and try again."
        ),
        "permission_denied": (
            "Hearth cannot access the capture device. Check its device permissions."
        ),
        "device_unavailable": (
            "The selected capture device is no longer available. Reconnect it and try again."
        ),
        "unsupported_mode": "The selected capture device rejected the requested video mode.",
        "io_error": "The capture device reported an input/output error.",
        "open_failed": "mpv could not open the selected capture input.",
    }

    def __init__(self, category: str, detail: str, reason: str = "error") -> None:
        self.category = category if category in self._USER_MESSAGES else "open_failed"
        self.detail = " ".join(detail.split())[:240]
        self.reason = reason[:40]
        self.user_message = self._USER_MESSAGES[self.category]
        super().__init__(self.user_message)


def classify_mpv_input_error(
    reason: object, file_error: object, diagnostic_log: object = ""
) -> ViewerInputOpenError | None:
    """Map mpv's end-file event and bounded error log into a stable category."""
    reason_text = reason if isinstance(reason, str) else ""
    if reason_text != "error":
        return None
    event_detail = file_error if isinstance(file_error, str) else ""
    log_detail = diagnostic_log if isinstance(diagnostic_log, str) else ""
    full_detail = " | ".join(part for part in (event_detail, log_detail) if part)
    detail = " ".join(full_detail.split())[:240]
    normalized = full_detail.casefold()
    if any(
        token in normalized
        for token in ("device or resource busy", "resource busy", "device busy", "ebusy")
    ):
        category = "device_busy"
    elif any(
        token in normalized
        for token in ("permission denied", "operation not permitted", "eacces", "eperm")
    ):
        category = "permission_denied"
    elif any(
        token in normalized for token in ("no such file", "no such device", "not found", "enoent")
    ):
        category = "device_unavailable"
    elif any(
        token in normalized
        for token in ("not a video capture device", "invalid argument", "unsupported")
    ):
        category = "unsupported_mode"
    elif any(token in normalized for token in ("input/output error", "i/o error", "eio")):
        category = "io_error"
    else:
        category = "open_failed"
    return ViewerInputOpenError(category, detail or "mpv reported an input error", reason_text)


@dataclass(frozen=True, slots=True)
class FirstFrame:
    """Evidence that mpv has a decoded current frame and configured video output."""

    width: int
    height: int
    picture_type: str


def create_private_mpv_log_file(runtime_directory: Path) -> Path:
    """Create a mode-0600, session-only mpv diagnostic log inside a private dir."""
    directory = runtime_directory.resolve()
    try:
        metadata = directory.stat()
    except OSError as exc:
        raise ViewerIPCError(f"Viewer runtime directory cannot be inspected: {exc}") from exc
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or metadata.st_mode & 0o077
    ):
        raise ViewerIPCError("Viewer runtime directory is not private and owner-controlled")
    log_path = directory / "mpv.log"
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_CLOEXEC", 0)
    fd: int | None = None
    created = False
    try:
        fd = os.open(log_path, flags, 0o600)
        created = True
        os.close(fd)
        fd = None
        log_path.chmod(0o600)
    except OSError as exc:
        if fd is not None:
            with suppress(OSError):
                os.close(fd)
        if created:
            with suppress(OSError):
                log_path.unlink()
        raise ViewerIPCError(f"Could not create private mpv log: {exc}") from exc
    return log_path


def cleanup_stale_capture_ipc_directories(
    runtime_root: Path,
    *,
    min_age_s: float = 60.0,
    now: float | None = None,
) -> tuple[Path, ...]:
    """Remove only old, private Hearth capture dirs with no live mpv socket.

    This is a bounded, one-shot recovery sweep called only on explicit Start.
    It never kills a process, touches a device/audio graph, follows symlinks,
    or removes a directory whose socket accepts a connection. Unknown socket
    errors and directories that fail ownership/permission checks are preserved.
    """
    if not math.isfinite(min_age_s) or min_age_s <= 0:
        raise ValueError("min_age_s must be a positive finite value")
    if now is not None and not math.isfinite(now):
        raise ValueError("now must be a finite timestamp")
    root = Path(runtime_root)
    try:
        root_info = root.lstat()
    except OSError:
        return ()
    if (
        not stat.S_ISDIR(root_info.st_mode)
        or root_info.st_uid != os.getuid()
        or root_info.st_mode & 0o077
    ):
        return ()

    current_time = time.time() if now is None else now
    removed: list[Path] = []
    candidates: list[Path] = []
    try:
        for index, entry in enumerate(root.iterdir()):
            # Bound directory enumeration and socket probes on explicit Start.
            # If the runtime root is unexpectedly crowded, fail closed.
            if index >= 512:
                return ()
            if entry.name.startswith("hearth-capture-"):
                if len(candidates) >= 16:
                    return ()
                candidates.append(entry)
    except OSError:
        return ()
    for directory in candidates:
        try:
            info = directory.lstat()
        except OSError:
            continue
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_mode & 0o077
            or current_time - info.st_mtime < min_age_s
        ):
            continue

        # Only remove a directory whose complete contents match artifacts
        # Hearth itself creates. A similarly named user directory with an
        # unknown file, nested directory, symlink, or unexpected owner/mode
        # is not ours to clean up.
        try:
            entries = tuple(directory.iterdir())
        except OSError:
            continue
        known_names = {"frame-health.lua", "mpv.log", "mpv.sock"}
        if any(entry.name not in known_names for entry in entries):
            continue
        entries_are_private = True
        for entry in entries:
            try:
                entry_info = entry.lstat()
            except OSError:
                entries_are_private = False
                break
            if entry_info.st_uid != os.getuid():
                entries_are_private = False
                break
            if entry.name == "mpv.sock":
                if not stat.S_ISSOCK(entry_info.st_mode):
                    entries_are_private = False
                    break
            elif not stat.S_ISREG(entry_info.st_mode) or entry_info.st_mode & 0o077:
                entries_are_private = False
                break
        if not entries_are_private:
            continue

        ipc_socket = directory / "mpv.sock"
        try:
            socket_info = ipc_socket.lstat()
        except FileNotFoundError:
            socket_info = None
        except OSError:
            continue
        if socket_info is not None and not stat.S_ISSOCK(socket_info.st_mode):
            continue

        if socket_info is not None:
            probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            probe.settimeout(0.1)
            try:
                probe.connect(str(ipc_socket))
            except OSError as exc:
                if exc.errno not in (ECONNREFUSED, ENOENT):
                    continue
            else:
                continue
            finally:
                probe.close()

        try:
            shutil.rmtree(directory)
        except OSError:
            continue
        removed.append(directory)
    return tuple(removed)


def create_private_ipc_directory() -> Path:
    """Create a session-private IPC directory under the owner's runtime dir."""
    runtime = os.environ.get("XDG_RUNTIME_DIR", "").strip()
    if not runtime:
        raise ViewerIPCError("XDG_RUNTIME_DIR is unavailable; refusing an insecure IPC fallback")
    root = Path(runtime)
    try:
        metadata = root.lstat()
    except OSError as exc:
        raise ViewerIPCError(f"XDG_RUNTIME_DIR cannot be inspected: {exc}") from exc
    if not stat.S_ISDIR(metadata.st_mode):
        raise ViewerIPCError("XDG_RUNTIME_DIR is not a directory")
    if metadata.st_uid != os.getuid():
        raise ViewerIPCError("XDG_RUNTIME_DIR is not owned by the current user")
    if metadata.st_mode & 0o077:
        raise ViewerIPCError("XDG_RUNTIME_DIR permissions are not private")
    # A one-shot, fail-closed cleanup of old session-private artifacts is safe
    # only here, on explicit Start. Never run a watcher or sweep on app launch.
    cleanup_stale_capture_ipc_directories(root)
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
        self._frame_health: FrameHealthSnapshot | None = None
        self._no_signal = False
        self._no_signal_callback: Callable[[bool], None] | None = None
        self.mpv_log_path = runtime_directory / "mpv.log"

    @property
    def pid(self) -> int | None:
        return getattr(self._process, "pid", None)

    def poll(self) -> int | None:
        return self._process.poll()

    @property
    def frame_health(self) -> FrameHealthSnapshot | None:
        """Latest validated pixel-health metadata, if the Lua probe is available."""
        return self._frame_health

    @property
    def no_signal(self) -> bool:
        """Whether the active viewer currently displays its timestamp-stall card."""
        return self._no_signal

    def set_no_signal_callback(self, callback: Callable[[bool], None] | None) -> None:
        """Register an optional event callback for no-signal state transitions."""
        self._no_signal_callback = callback

    def _set_no_signal(self, value: bool) -> None:
        if self._no_signal == value:
            return
        self._no_signal = value
        callback = self._no_signal_callback
        if callback is not None:
            # UI observers must never break the stream monitor or session.
            with suppress(Exception):
                callback(value)

    @staticmethod
    def _send_no_signal(connection: socket.socket, value: bool) -> None:
        command = {"command": ["set_property", "user-data/hearth/no-signal", value]}
        connection.sendall((json.dumps(command, separators=(",", ":")) + "\n").encode("utf-8"))

    def wait(self, timeout: float | None = None) -> int:
        return self._process.wait(timeout=timeout)

    def monitor_until_exit(self, stall_timeout_s: float = 2.0) -> int:
        """Wait for child exit while observing event-driven video timestamp progress.

        This monitor is intended to run only in the active session's existing
        viewer-wait thread, after `wait_for_first_frame()` succeeded. It keeps
        one local IPC connection open and uses `observe_property(time-pos)`;
        there is no timer-driven query loop. If timestamps stop changing for
        `stall_timeout_s`, it displays the "Video stopped updating" card and keeps waiting.
        Resumed timestamps clear the card; only process exit ends the session.
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
        last_progress = time.monotonic()
        no_signal = False
        request_id = 4001
        frame_health_request_id = 4002
        try:
            for subscription_id, property_id, property_name in (
                (request_id, 1, "time-pos"),
                (frame_health_request_id, 2, "user-data/hearth/frame-health"),
            ):
                connection.sendall(
                    (
                        json.dumps(
                            {
                                "command": ["observe_property", property_id, property_name],
                                "request_id": subscription_id,
                            },
                            separators=(",", ":"),
                        )
                        + "\n"
                    ).encode("utf-8")
                )
            acknowledged = False
            while True:
                remaining = stall_timeout_s - (time.monotonic() - last_progress)
                timeout = None if no_signal else max(0.0, remaining)
                readable, _, _ = select.select([connection], [], [], timeout)
                if not readable:
                    self._send_no_signal(connection, True)
                    self._set_no_signal(True)
                    no_signal = True
                    continue
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
                    if message.get("request_id") == request_id:
                        if message.get("error") != "success":
                            raise ViewerIPCError(
                                f"mpv could not observe video timestamps: {message.get('error')}"
                            )
                        acknowledged = True
                        last_progress = time.monotonic()
                        continue
                    if message.get("request_id") == frame_health_request_id:
                        # Frame health is advisory; older or restricted mpv builds
                        # may not expose the user-data property. Timestamp
                        # monitoring remains the required liveness signal.
                        continue
                    if message.get("event") == "end-file" and message.get("reason") == "error":
                        # An mpv input error is a hard stream failure (for
                        # example, an unplugged device), not a recoverable
                        # timestamp stall. Classify it and let the session
                        # manager run normal cleanup instead of waiting forever.
                        if no_signal:
                            with suppress(OSError):
                                self._send_no_signal(connection, False)
                            self._set_no_signal(False)
                            no_signal = False
                        input_error = self._classify_input_error("error", message.get("file_error"))
                        if input_error is not None:
                            raise input_error
                        continue
                    if message.get("event") != "property-change":
                        continue
                    if message.get("id") == 2:
                        health = parse_frame_health_payload(message.get("data"))
                        if health is not None:
                            self._frame_health = health
                        continue
                    if message.get("id") != 1:
                        continue
                    position = message.get("data")
                    if (
                        isinstance(position, bool)
                        or not isinstance(position, (int, float))
                        or not math.isfinite(position)
                    ):
                        continue
                    numeric_position = float(position)
                    if last_position is None or numeric_position != last_position:
                        last_position = numeric_position
                        if acknowledged:
                            last_progress = time.monotonic()
                            if no_signal:
                                self._send_no_signal(connection, False)
                                self._set_no_signal(False)
                                no_signal = False
        except (ConnectionError, OSError) as exc:
            if self.poll() is not None:
                return self._process.wait(timeout=1.0)
            raise ViewerIPCError(f"mpv stream monitor connection failed: {exc}") from exc
        finally:
            connection.close()

    def _read_error_log(self, *, max_bytes: int = 8192) -> str:
        """Read only recent mpv error/fatal lines from the private session log."""
        try:
            with self.mpv_log_path.open("rb") as stream:
                stream.seek(0, os.SEEK_END)
                size = stream.tell()
                stream.seek(max(0, size - max_bytes), os.SEEK_SET)
                tail = stream.read(max_bytes).decode("utf-8", errors="replace")
        except OSError:
            return ""
        error_lines = [
            " ".join(line.split())
            for line in tail.splitlines()
            if "[e]" in line.casefold() or "[fatal]" in line.casefold()
        ]
        return " | ".join(error_lines[-8:])[:1024]

    def _classify_input_error(
        self, reason: object, file_error: object = ""
    ) -> ViewerInputOpenError | None:
        return classify_mpv_input_error(reason, file_error, self._read_error_log())

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
            returncode = self.poll()
            if returncode is not None:
                input_error = self._classify_input_error("error" if returncode != 0 else "eof")
                if input_error is not None:
                    raise input_error
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
            except (ConnectionError, BrokenPipeError, OSError, TimeoutError, ValueError) as exc:
                returncode = self.poll()
                if returncode is not None:
                    input_error = self._classify_input_error("error" if returncode != 0 else "eof")
                    if input_error is not None:
                        raise input_error from exc
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
                return FirstFrame(parameters["w"], parameters["h"], picture)
            time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))

    def _get_property(
        self,
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
            if not isinstance(response, Mapping):
                continue
            if response.get("event") == "end-file":
                input_error = self._classify_input_error(
                    response.get("reason"), response.get("file_error")
                )
                if input_error is not None:
                    raise input_error
                continue
            if response.get("request_id") != request_id:
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
