"""Client side of the control socket.

Used by the CLI: a second ``hearth`` invocation hands its command to the
running instance instead of starting a second mixer.
"""

from __future__ import annotations

import contextlib
import errno
import logging
import os
import socket
import stat
from pathlib import Path

from hearth import paths
from hearth.errors import IpcError
from hearth.ipc import protocol
from hearth.ipc.path_lock import socket_path_lock

log = logging.getLogger(__name__)


def send(command: str, *, socket_path: Path | None = None, timeout: float = 0.5) -> str | None:
    """Send *command* and return the reply, or ``None`` if nobody answered.

    ``None`` means "no running instance" and is a normal outcome, not an
    error: it is how the CLI decides to start the GUI itself.
    """
    path = socket_path or paths.socket_file()
    if not path.exists():
        return None
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)  # type: ignore[attr-defined]
    sock.settimeout(timeout)
    try:
        sock.connect(str(path))
        sock.sendall(protocol.encode(command))
        return protocol.read_line(sock)
    except (OSError, TimeoutError, IpcError) as exc:
        # A socket file left behind by a crashed instance is the common case.
        log.debug("IPC send %r failed: %s", command, exc)
        return None
    finally:
        sock.close()


def is_running(*, socket_path: Path | None = None) -> bool:
    """True when an instance answers on the socket."""
    return send("show", socket_path=socket_path) is not None


def clear_stale_socket(socket_path: Path | None = None) -> bool:
    """Remove a stale socket while serializing with competing app startups."""
    path = socket_path or paths.socket_file()
    try:
        with socket_path_lock(path):
            return _clear_stale_socket_locked(path)
    except OSError as exc:
        log.debug("could not lock IPC path %s for stale cleanup: %s", path, exc)
        return False


def _clear_stale_socket_locked(path: Path) -> bool:
    """Remove a stale Unix socket without unlinking a live or unrelated path.

    A reply timeout does not mean a listener is dead: it may simply be busy.
    A successful connect proves that something is listening, even when its
    handler takes longer than the normal CLI timeout to reply.
    """
    try:
        initial = path.lstat()
    except FileNotFoundError:
        return False
    except OSError as exc:
        log.debug("could not inspect IPC path %s: %s", path, exc)
        return False

    if not stat.S_ISSOCK(initial.st_mode):
        return False

    probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)  # type: ignore[attr-defined]
    connected = False
    try:
        probe.settimeout(protocol.TIMEOUT)
        try:
            probe.connect(str(path))
            connected = True
        except OSError as exc:
            if exc.errno not in (errno.ECONNREFUSED, errno.ENOENT):
                log.debug("could not safely probe IPC path %s: %s", path, exc)
                return False
        if connected:
            # Keep the current instance's UX: ask a live mixer to focus itself.
            # Whether it replies quickly is not used to determine liveness.
            with contextlib.suppress(OSError, TimeoutError, IpcError):
                probe.sendall(protocol.encode("show"))
                protocol.read_line(probe)
    finally:
        probe.close()

    if connected:
        return False

    # Only unlink the exact stale socket inspected before connecting. If a
    # concurrent launcher replaced the path, leave it alone.
    try:
        current = path.lstat()
    except FileNotFoundError:
        return False
    except OSError as exc:
        log.debug("could not re-check IPC path %s: %s", path, exc)
        return False
    if not stat.S_ISSOCK(current.st_mode) or (
        current.st_dev,
        current.st_ino,
    ) != (initial.st_dev, initial.st_ino):
        return False

    try:
        path.unlink()
    except OSError as exc:  # pragma: no cover - permissions on our own dir
        log.warning("could not remove stale socket %s: %s", path, exc)
        return False
    return True


# Windows named pipes are authenticated and have kernel-managed lifetimes.
if os.name == "nt":
    from hearth.ipc.windows_pipe import (
        clear_stale_socket as _windows_clear_stale,
    )
    from hearth.ipc.windows_pipe import (
        is_running as _windows_is_running,
    )
    from hearth.ipc.windows_pipe import (
        send as _windows_pipe_send,
    )

    send = _windows_pipe_send
    is_running = _windows_is_running
    clear_stale_socket = _windows_clear_stale
