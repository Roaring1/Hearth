"""Server side of the control socket.

Runs on its own thread inside the GUI process. Two properties matter and are
both tested: a raising handler must not kill the listener (a previous
``except: break`` permanently disabled Hard Restart, SSH, dump and unmute),
and shutdown must be clean rather than leaving a socket file behind.
"""

from __future__ import annotations

import contextlib
import errno
import logging
import os
import socket
import stat
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path

from hearth import paths
from hearth.errors import IpcError
from hearth.ipc import protocol
from hearth.ipc.path_lock import socket_path_lock

log = logging.getLogger(__name__)

Handler = Callable[[str], str | None]

#: How often the accept loop checks the stop flag, seconds.
ACCEPT_POLL = 0.25


class Server:
    """Accept loop for the control socket."""

    def __init__(self, handler: Handler, *, socket_path: Path | None = None) -> None:
        self._handler = handler
        self.path = socket_path or paths.socket_file()
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stopping = threading.Event()
        self._owns_path = False
        self._path_identity: tuple[int, int] | None = None

    def bind(self) -> None:
        """Bind under a cross-process lock so a racer cannot unlink our socket."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with socket_path_lock(self.path):
            self._bind_locked()

    def _bind_locked(self) -> None:
        """Perform stale-path inspection and bind while holding the path lock."""
        try:
            existing_stat = self.path.lstat()
        except FileNotFoundError:
            existing_stat = None

        if existing_stat is not None:
            if not stat.S_ISSOCK(existing_stat.st_mode):
                raise FileExistsError(
                    errno.EEXIST,
                    "refusing to replace a non-socket IPC path",
                    str(self.path),
                )

            probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            connected = False
            try:
                probe.settimeout(0.25)
                try:
                    probe.connect(str(self.path))
                    connected = True
                except OSError as exc:
                    if exc.errno not in (errno.ECONNREFUSED, errno.ENOENT):
                        raise OSError(
                            f"cannot safely check existing IPC socket {self.path}: {exc}"
                        ) from exc
                if connected:
                    # Let a live instance focus itself, then refuse to replace
                    # its pathname even if it does not manage to reply.
                    with contextlib.suppress(OSError, TimeoutError, IpcError):
                        probe.sendall(protocol.encode("show"))
                        protocol.read_line(probe)
            finally:
                probe.close()

            if connected:
                raise OSError(
                    errno.EADDRINUSE,
                    "Hearth IPC socket already has a listener",
                    str(self.path),
                )

            # Only unlink the exact stale socket we examined. If another
            # process replaced the pathname during the probe, fail closed.
            try:
                current_stat = self.path.lstat()
            except FileNotFoundError:
                current_stat = None
            if current_stat is not None:
                same_socket = stat.S_ISSOCK(current_stat.st_mode) and (
                    current_stat.st_dev,
                    current_stat.st_ino,
                ) == (existing_stat.st_dev, existing_stat.st_ino)
                if not same_socket:
                    raise FileExistsError(
                        errno.EEXIST,
                        "IPC path changed while checking for a stale socket",
                        str(self.path),
                    )
                self.path.unlink()

        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.bind(str(self.path))
            created_stat = self.path.lstat()
            self._owns_path = True
            self._path_identity = (created_stat.st_dev, created_stat.st_ino)
            sock.listen(4)
            self.path.chmod(0o600)
            # Closing a listening socket from another thread does not reliably
            # wake a blocked accept() on Linux, which left the IPC thread running
            # after stop(). A short accept timeout makes shutdown deterministic.
            sock.settimeout(ACCEPT_POLL)
            self._sock = sock
        except BaseException:
            sock.close()
            self._remove_owned_path_locked()
            raise

    def _remove_owned_path(self) -> None:
        """Remove this server's socket path under the shared maintenance lock."""
        with socket_path_lock(self.path):
            self._remove_owned_path_locked()

    def _remove_owned_path_locked(self) -> None:
        """Unlink the socket only if the pathname still names our inode."""
        identity = self._path_identity
        if not self._owns_path or identity is None:
            return
        try:
            current = self.path.lstat()
            if stat.S_ISSOCK(current.st_mode) and (current.st_dev, current.st_ino) == identity:
                self.path.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            log.warning("could not remove owned IPC socket %s: %s", self.path, exc)
        finally:
            self._owns_path = False
            self._path_identity = None

    def serve_forever(self) -> None:
        """Accept connections until :meth:`stop` is called."""
        if self._sock is None:
            self.bind()
        sock = self._sock
        if sock is None:  # pragma: no cover - bind() always sets it
            raise OSError("IPC socket was not created")
        while not self._stopping.is_set():
            try:
                conn, _ = sock.accept()
            except TimeoutError:
                continue  # nothing connected; recheck the stop flag
            except OSError:
                break  # listening socket closed by stop()
            self._handle(conn)

    def _handle(self, conn: socket.socket) -> None:
        try:
            conn.settimeout(protocol.TIMEOUT)
            request = protocol.read_line(conn)
            reply = self._handler(request) or protocol.OK
            conn.sendall(protocol.encode(str(reply)))
        except Exception as exc:
            # One bad command must never take down the listener.
            log.warning("IPC handler error for request: %s", exc)
        finally:
            with contextlib.suppress(OSError):
                conn.close()

    def start(self) -> threading.Thread:
        """Bind and start under one lock so a failing startup cannot orphan a racer.

        If the OS refuses to create the worker, release the listening socket
        and remove only the socket inode this instance created before another
        launcher is allowed to inspect or bind the pathname.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with socket_path_lock(self.path):
            self._bind_locked()
            thread = threading.Thread(target=self.serve_forever, daemon=True, name="hearth-ipc")
            try:
                thread.start()
            except BaseException as exc:
                if self._sock is not None:
                    with contextlib.suppress(OSError):
                        self._sock.close()
                    self._sock = None
                self._remove_owned_path_locked()
                if isinstance(exc, RuntimeError):
                    raise OSError("could not start the IPC listener thread") from exc
                raise
            self._thread = thread
            return thread

    def stop(self, *, timeout: float = 2.0) -> None:
        """Stop accepting, join the thread, and remove the socket file."""
        self._stopping.set()
        if self._sock is not None:
            with contextlib.suppress(OSError):  # pragma: no cover
                self._sock.close()
            self._sock = None
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None
        self._remove_owned_path()


def write_pid(pid_path: Path | None = None) -> Path:
    """Write a private PID fallback file atomically without following symlinks."""
    path = pid_path or paths.pid_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        metadata = None
    if metadata is not None and not stat.S_ISREG(metadata.st_mode):
        raise OSError(f"refusing to replace non-regular PID path: {path}")

    fd, tmp_name = tempfile.mkstemp(prefix=f"{path.name}.tmp.", dir=path.parent)
    tmp = Path(tmp_name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(f"{os.getpid()}\n")
            stream.flush()
            os.fsync(stream.fileno())
        tmp.replace(path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return path


def read_pid(pid_path: Path | None = None) -> int | None:
    path = pid_path or paths.pid_file()
    try:
        return int(path.read_text().strip())
    except (OSError, ValueError):
        return None


def remove_pid(pid_path: Path | None = None, *, expected_pid: int | None = None) -> bool:
    """Remove a PID record only when it still belongs to the expected process."""
    path = pid_path or paths.pid_file()
    expected = os.getpid() if expected_pid is None else expected_pid
    try:
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode):
            return False
        actual = int(path.read_text().strip())
    except FileNotFoundError:
        return False
    except (OSError, ValueError) as exc:
        log.debug("could not inspect PID file %s: %s", path, exc)
        return False
    if actual != expected:
        return False
    try:
        current = path.lstat()
    except FileNotFoundError:
        return False
    except OSError as exc:
        log.warning("could not re-check PID file %s: %s", path, exc)
        return False
    if (current.st_dev, current.st_ino) != (before.st_dev, before.st_ino):
        return False
    try:
        path.unlink()
    except OSError as exc:
        log.warning("could not remove PID file %s: %s", path, exc)
        return False
    return True


def pid_alive(pid: int | None) -> bool:
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, owned by someone else
    return True
