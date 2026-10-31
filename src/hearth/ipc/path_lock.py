"""Cross-process serialization for operations that inspect an IPC socket path."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from hearth import paths
from hearth.platform.locking import lock_file, unlock_file


@contextmanager
def socket_path_lock(path: Path) -> Iterator[None]:
    """Lock socket-path maintenance across concurrent Hearth processes.

    The lock file is persistent by design: removing it after unlock would let
    another process hold a different inode and defeat mutual exclusion. It is
    kept owner-only and refuses symlinks, non-regular files, foreign owners,
    and hard-linked lock files before taking the advisory lock.
    """
    lock_path = path.with_name(f".{path.name}.lock")
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(lock_path, flags, 0o600)
    try:
        paths.ensure_private_fd(fd, lock_path)
        os.fchmod(fd, 0o600)  # type: ignore[attr-defined]
        lock_file(fd)
        try:
            yield
        finally:
            unlock_file(fd)
    finally:
        os.close(fd)
