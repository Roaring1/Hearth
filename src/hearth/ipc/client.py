"""Client side of the control socket.

Used by the CLI: a second ``hearth`` invocation hands its command to the
running instance instead of starting a second mixer.
"""

from __future__ import annotations

import contextlib
import errno
import logging
import socket
import stat
from pathlib import Path

from hearth import paths
from hearth.ipc import protocol

log = logging.getLogger(__name__)


def send(command: str, *, socket_path: Path | None = None, timeout: float = 0.5) -> str | None:
    """Send *command* and return the reply, or ``None`` if nobody answered.

    ``None`` means "no running instance" and is a normal outcome, not an
    error: it is how the CLI decides to start the GUI itself.
    """
    path = socket_path or paths.socket_file()
    if not path.exists():
        return None
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect(str(path))
        sock.sendall(protocol.encode(command))
        return protocol.read_line(sock)
    except (OSError, TimeoutError) as exc:
        # A socket file left behind by a crashed instance is the common case.
        log.debug("IPC send %r failed: %s", command, exc)
        return None
    finally:
        sock.close()


def is_running(*, socket_path: Path | None = None) -> bool:
    """True when an instance answers on the socket."""
    return send("show", socket_path=socket_path) is not None


def clear_stale_socket(socket_path: Path | None = None) -> bool:
    """Remove a stale Unix socket without unlinking a live or unrelated path.

    A reply timeout does not mean a listener is dead: it may simply be busy.
    A successful connect proves that something is listening, even when its
    handler takes longer than the normal CLI timeout to reply.
    """
    path = socket_path or paths.socket_file()
    try:
        initial = path.lstat()
    except FileNotFoundError:
        return False
    except OSError as exc:
        log.debug("could not inspect IPC path %s: %s", path, exc)
        return False

    if not stat.S_ISSOCK(initial.st_mode):
        return False

    probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
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
            with contextlib.suppress(OSError, TimeoutError):
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
