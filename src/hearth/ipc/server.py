"""Server side of the control socket.

Runs on its own thread inside the GUI process. Two properties matter and are
both tested: a raising handler must not kill the listener (a previous
``except: break`` permanently disabled Hard Restart, SSH, dump and unmute),
and shutdown must be clean rather than leaving a socket file behind.
"""

from __future__ import annotations

import contextlib
import logging
import os
import socket
import threading
from collections.abc import Callable
from pathlib import Path

from hearth import paths
from hearth.ipc import protocol

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

    def bind(self) -> None:
        """Create and bind the listening socket.

        The socket is chmod 0600: anything that can write to it can mute the
        machine or quit the app.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            try:
                self.path.unlink()
            except OSError as exc:
                raise OSError(f"cannot replace socket {self.path}: {exc}") from exc
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.bind(str(self.path))
        sock.listen(4)
        self.path.chmod(0o600)
        # Closing a listening socket from another thread does not reliably
        # wake a blocked accept() on Linux, which left the IPC thread running
        # after stop(). A short accept timeout makes shutdown deterministic.
        sock.settimeout(ACCEPT_POLL)
        self._sock = sock

    def serve_forever(self) -> None:
        """Accept connections until :meth:`stop` is called."""
        if self._sock is None:
            self.bind()
        if self._sock is None:  # pragma: no cover - bind() always sets it
            raise OSError("IPC socket was not created")
        while not self._stopping.is_set():
            try:
                conn, _ = self._sock.accept()
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
        """Run :meth:`serve_forever` on a background thread."""
        self.bind()
        thread = threading.Thread(target=self.serve_forever, daemon=True, name="hearth-ipc")
        thread.start()
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
        with contextlib.suppress(OSError):  # pragma: no cover
            if self.path.exists():
                self.path.unlink()


def write_pid(pid_path: Path | None = None) -> Path:
    path = pid_path or paths.pid_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{os.getpid()}\n")
    return path


def read_pid(pid_path: Path | None = None) -> int | None:
    path = pid_path or paths.pid_file()
    try:
        return int(path.read_text().strip())
    except (OSError, ValueError):
        return None


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
