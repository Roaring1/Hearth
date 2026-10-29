"""Cross-process serialization for operations that inspect an IPC socket path."""

from __future__ import annotations

import fcntl
import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


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
        metadata = os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode):
            raise OSError(f"IPC path lock is not a regular file: {lock_path}")
        if metadata.st_uid != os.getuid():
            raise PermissionError(f"IPC path lock is not owned by this user: {lock_path}")
        if metadata.st_nlink != 1:
            raise OSError(f"IPC path lock must not be hard-linked: {lock_path}")
        os.fchmod(fd, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)
