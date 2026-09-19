"""Client side of the control socket.

Used by the CLI: a second ``hearth`` invocation hands its command to the
running instance instead of starting a second mixer.
"""

from __future__ import annotations

import logging
import socket
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
    """Remove a socket file that no process is listening on.

    Returns True when a stale file was removed. Never removes a live socket.
    """
    path = socket_path or paths.socket_file()
    if not path.exists():
        return False
    if send("show", socket_path=path) is not None:
        return False
    try:
        path.unlink()
    except OSError as exc:  # pragma: no cover - permissions on our own dir
        log.warning("could not remove stale socket %s: %s", path, exc)
        return False
    return True
