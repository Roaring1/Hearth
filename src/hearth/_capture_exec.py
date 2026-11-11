"""Standard-library exec wrapper for Linux parent-thread death signaling.

The parent passes a readiness-pipe fd with FD_CLOEXEC semantics and supplies
its pid in the environment. The wrapper installs PDEATHSIG, checks for the
parent-death race, writes readiness, then execs the actual viewer. A failed
exec writes an error before exiting so the parent can distinguish it from a
successful exec (which closes the readiness fd atomically).
"""

from __future__ import annotations

import contextlib
import ctypes
import os
import signal
import sys

_PR_SET_PDEATHSIG = 1


def _notify(fd: int, message: bytes) -> None:
    with contextlib.suppress(OSError):
        os.write(fd, message)


def install_parent_death_signal(expected_parent_pid: int) -> bool:
    """Set SIGTERM on parent-thread death, then detect the pre-prctl race."""
    if not sys.platform.startswith("linux"):
        raise OSError("parent-death signaling is supported only on Linux")
    libc = ctypes.CDLL(None, use_errno=True)
    prctl = libc.prctl
    prctl.restype = ctypes.c_int
    result = prctl(
        ctypes.c_int(_PR_SET_PDEATHSIG),
        ctypes.c_ulong(signal.SIGTERM),
        ctypes.c_ulong(0),
        ctypes.c_ulong(0),
        ctypes.c_ulong(0),
    )
    if result != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))
    return os.getppid() == expected_parent_pid


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) < 2 or args[0] != "--":
        return 64
    command = args[1:]
    if not command or not command[0].strip():
        return 64
    try:
        expected_parent_pid = int(os.environ["HEARTH_CAPTURE_PARENT_PID"])
        ready_fd = int(os.environ["HEARTH_CAPTURE_READY_FD"])
    except (KeyError, ValueError):
        return 64
    if expected_parent_pid <= 1 or ready_fd < 0:
        return 64

    try:
        parent_alive = install_parent_death_signal(expected_parent_pid)
    except OSError as exc:
        _notify(ready_fd, f"Eparent-death setup failed: {exc}".encode("utf-8", "replace"))
        return 126
    if not parent_alive:
        _notify(ready_fd, b"Eparent exited before PDEATHSIG was armed")
        return 125

    # Close the handshake fd atomically with successful exec. If exec fails,
    # it remains available so the parent receives an explicit E... message.
    os.set_inheritable(ready_fd, False)
    _notify(ready_fd, b"R")
    os.environ.pop("HEARTH_CAPTURE_PARENT_PID", None)
    os.environ.pop("HEARTH_CAPTURE_READY_FD", None)
    try:
        os.execvpe(command[0], command, os.environ)  # noqa: S606 - argv-only exec, never a shell
    except OSError as exc:
        _notify(ready_fd, f"Eexec failed: {exc}".encode("utf-8", "replace"))
        return 127


if __name__ == "__main__":  # pragma: no cover - exercised through proc.spawn_owned
    raise SystemExit(main())
